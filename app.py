from flask import Flask, request, jsonify, send_file, render_template
from flask_sqlalchemy import SQLAlchemy
import os
import zipfile
import io
import uuid
from datetime import datetime

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///codecollector.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'Uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB limit

db = SQLAlchemy(app)

# Database Model
class Project(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    file_count = db.Column(db.Integer)
    total_size = db.Column(db.Float)
    status_file = db.Column(db.String(255))
    code_file = db.Column(db.String(255))

# Create uploads folder
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

def is_valid_text_file(content):
    """Check if the content is likely a text file by checking for non-printable characters."""
    try:
        # Sample first 1024 bytes to check if it's text
        sample = content[:1024].decode('utf-8', errors='ignore')
        # Check for excessive non-printable characters
        non_printable = sum(1 for c in sample if ord(c) < 32 and c not in '\n\r\t')
        return non_printable / len(sample) < 0.1  # Allow up to 10% non-printable chars
    except UnicodeDecodeError:
        return False

def process_files(files, extensions, output_filename):
    status_content = []
    code_content = []
    file_count = 0
    total_size = 0

    # If extensions provided, normalize to lowercase and remove leading dots
    allowed_extensions = [ext.strip().lower().lstrip('.') for ext in extensions.split(',')] if extensions else []

    for file in files:
        filename = file.filename.lower()
        
        if filename.endswith('.zip'):
            try:
                # Process ZIP file
                with zipfile.ZipFile(file, 'r') as zip_ref:
                    for zip_info in zip_ref.infolist():
                        zip_filename = zip_info.filename.lower()
                        # Skip macOS metadata files
                        if '__macosx' in zip_filename or zip_filename.startswith('._'):
                            status_content.append(f'Skipped: {zip_info.filename} - macOS metadata file')
                            continue
                        if not zip_info.is_dir():
                            ext = os.path.splitext(zip_filename)[1][1:].lower()
                            # Process all files if no extensions specified, or if extension matches
                            if not allowed_extensions or ext in allowed_extensions:
                                try:
                                    with zip_ref.open(zip_info) as f:
                                        content = f.read()
                                        if is_valid_text_file(content):
                                            text_content = content.decode('utf-8', errors='ignore')
                                            code_content.append(f'// File: {zip_info.filename}\n{text_content}\n\n')
                                            status_content.append(f'Processed: {zip_info.filename}')
                                            file_count += 1
                                            total_size += zip_info.file_size / 1024  # KB
                                        else:
                                            status_content.append(f'Skipped: {zip_info.filename} - Binary or non-text file')
                                except Exception as e:
                                    status_content.append(f'Failed: {zip_info.filename} - {str(e)}')
            except Exception as e:
                status_content.append(f'Failed to process ZIP file {filename} - {str(e)}')
        else:
            # Process individual file
            ext = os.path.splitext(filename)[1][1:].lower()
            # Skip macOS metadata files
            if filename.startswith('._'):
                status_content.append(f'Skipped: {filename} - macOS metadata file')
                continue
            # Process all files if no extensions specified, or if extension matches
            if not allowed_extensions or ext in allowed_extensions:
                try:
                    content = file.read()
                    if is_valid_text_file(content):
                        text_content = content.decode('utf-8', errors='ignore')
                        code_content.append(f'// File: {filename}\n{text_content}\n\n')
                        status_content.append(f'Processed: {filename}')
                        file_count += 1
                        total_size += len(content) / 1024  # KB
                    else:
                        status_content.append(f'Skipped: {filename} - Binary or non-text file')
                except Exception as e:
                    status_content.append(f'Failed: {filename} - {str(e)}')

    if not file_count:
        status_content.append('No valid text files processed. Check file types or ZIP contents.')

    # Save files
    project_id = str(uuid.uuid4())
    status_path = os.path.join(app.config['UPLOAD_FOLDER'], f'status_{project_id}.txt')
    code_path = os.path.join(app.config['UPLOAD_FOLDER'], f'{output_filename or "project_code"}.txt')

    with open(status_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(status_content))
    
    if code_content:  # Only write code file if there's content
        with open(code_path, 'w', encoding='utf-8') as f:
            f.write(''.join(code_content))

    return {
        'file_count': file_count,
        'total_size': round(total_size, 2),
        'status_path': status_path,
        'code_path': code_path if code_content else None
    }

@app.route('/upload', methods=['POST'])
def upload():
    files = request.files.getlist('files')
    extensions = request.form.get('extensions', '')
    output_filename = request.form.get('output', 'project_code.txt')

    if not files:
        return jsonify({'success': False, 'message': 'No files uploaded'})

    result = process_files(files, extensions, output_filename)

    # Save to database
    project = Project(
        file_count=result['file_count'],
        total_size=result['total_size'],
        status_file=result['status_path'],
        code_file=result['code_path']
    )
    db.session.add(project)
    db.session.commit()

    response = {
        'success': result['file_count'] > 0,
        'file_count': result['file_count'],
        'total_size': result['total_size'],
        'status_url': f'/download/{os.path.basename(result["status_path"])}'
    }
    
    if result['code_path']:
        response['code_url'] = f'/download/{os.path.basename(result["code_path"])}'

    return jsonify(response)

@app.route('/download/<filename>')
def download(filename):
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return jsonify({'success': False, 'message': 'File not found'}), 404

@app.route('/files')
def get_files():
    files = []
    upload_folder = app.config['UPLOAD_FOLDER']
    for filename in os.listdir(upload_folder):
        if filename.endswith('.txt') and not filename.startswith('status_'):  # Exclude status files
            file_path = os.path.join(upload_folder, filename)
            mod_time = os.path.getmtime(file_path)
            files.append({
                'name': filename,
                'url': f'/download/{filename}',
                'mtime': mod_time
            })
    # Sort by modification time, newest first
    files.sort(key=lambda x: x['mtime'], reverse=True)
    return jsonify(files)

@app.route('/clear-files', methods=['POST'])
def clear_files():
    try:
        upload_folder = app.config['UPLOAD_FOLDER']
        for filename in os.listdir(upload_folder):
            if filename.endswith('.txt'):  # Only delete text files
                file_path = os.path.join(upload_folder, filename)
                os.remove(file_path)
        # Clear database entries
        Project.query.delete()
        db.session.commit()
        return jsonify({'success': True, 'message': 'All text files and database entries cleared'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error clearing files: {str(e)}'})

@app.route('/')
def home():
    return render_template('index.html')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5001)