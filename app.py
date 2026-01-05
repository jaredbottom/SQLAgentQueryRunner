import os
import pyodbc
import smtplib
import uuid
import time
import threading
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key')

# Configuration
SQL_SERVER = os.getenv('SQL_SERVER')
SQL_DATABASE = os.getenv('SQL_DATABASE', 'msdb')
SQL_USERNAME = os.getenv('SQL_USERNAME')
SQL_PASSWORD = os.getenv('SQL_PASSWORD')
NETWORK_DRIVE_ROOT = os.getenv('NETWORK_DRIVE_ROOT')
SMTP_SERVER = os.getenv('SMTP_SERVER')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
SMTP_USERNAME = os.getenv('SMTP_USERNAME')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD')
SMTP_FROM_EMAIL = os.getenv('SMTP_FROM_EMAIL')


def get_db_connection():
    """Create and return a database connection."""
    conn_str = (
        f'DRIVER={{ODBC Driver 17 for SQL Server}};'
        f'SERVER={SQL_SERVER};'
        f'DATABASE={SQL_DATABASE};'
        f'UID={SQL_USERNAME};'
        f'PWD={SQL_PASSWORD}'
    )
    return pyodbc.connect(conn_str)


def scan_directory_for_sql(path, pattern=''):
    """
    Recursively scan directory for SQL files and return a tree structure.
    Only include directories that contain SQL files.
    """
    try:
        result = {
            'name': os.path.basename(path) or path,
            'path': path,
            'type': 'directory',
            'children': []
        }

        items = []
        try:
            items = os.listdir(path)
        except (PermissionError, OSError):
            return None

        # Separate files and directories
        files = []
        dirs = []

        for item in sorted(items):
            item_path = os.path.join(path, item)

            if os.path.isfile(item_path) and item.lower().endswith('.sql'):
                # Apply pattern filter if provided
                if not pattern or pattern.lower() in item.lower():
                    files.append({
                        'name': item,
                        'path': item_path,
                        'type': 'file',
                        'size': os.path.getsize(item_path)
                    })
            elif os.path.isdir(item_path):
                dirs.append(item_path)

        # Recursively process subdirectories
        for dir_path in dirs:
            child = scan_directory_for_sql(dir_path, pattern)
            if child and (child.get('children') or child.get('type') == 'file'):
                result['children'].append(child)

        # Add files to current directory
        result['children'].extend(files)

        # Only return directory if it has children (files or subdirs with files)
        if result['children']:
            return result
        return None

    except Exception as e:
        print(f"Error scanning {path}: {str(e)}")
        return None


def create_agent_job(file_path, job_name=None):
    """
    Create a SQL Server Agent job to execute a SQL file.
    Returns the job_id.
    """
    if not job_name:
        job_name = f"RunSQLFile_{uuid.uuid4().hex[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    job_id = str(uuid.uuid4())

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Create the job
        cursor.execute("""
            EXEC msdb.dbo.sp_add_job
                @job_name = ?,
                @enabled = 1,
                @description = 'Auto-generated job to run SQL file',
                @delete_level = 1,
                @job_id = ? OUTPUT
        """, job_name, job_id)

        # Add job step
        step_command = f"EXEC run_query_file '{file_path}'"
        cursor.execute("""
            EXEC msdb.dbo.sp_add_jobstep
                @job_name = ?,
                @step_name = 'Execute SQL File',
                @subsystem = 'TSQL',
                @command = ?,
                @retry_attempts = 0,
                @retry_interval = 0
        """, job_name, step_command)

        # Add job to local server
        cursor.execute("""
            EXEC msdb.dbo.sp_add_jobserver
                @job_name = ?,
                @server_name = '(local)'
        """, job_name)

        conn.commit()

        # Start the job
        cursor.execute("""
            EXEC msdb.dbo.sp_start_job @job_name = ?
        """, job_name)

        conn.commit()

        return job_id, job_name

    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def get_job_status(job_name):
    """
    Check the status of a SQL Server Agent job.
    Returns: 'running', 'succeeded', 'failed', 'unknown'
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Check job execution status
        cursor.execute("""
            SELECT
                j.name,
                ja.run_status,
                ja.run_date,
                ja.run_time,
                CASE ja.run_status
                    WHEN 0 THEN 'Failed'
                    WHEN 1 THEN 'Succeeded'
                    WHEN 2 THEN 'Retry'
                    WHEN 3 THEN 'Canceled'
                    WHEN 4 THEN 'In Progress'
                    ELSE 'Unknown'
                END as status_text,
                ja.message
            FROM msdb.dbo.sysjobs j
            LEFT JOIN msdb.dbo.sysjobhistory ja ON j.job_id = ja.job_id
            WHERE j.name = ?
            AND ja.step_id = 0
            ORDER BY ja.run_date DESC, ja.run_time DESC
        """, job_name)

        row = cursor.fetchone()

        if row:
            run_status = row[1]
            message = row[5] if len(row) > 5 else ''

            if run_status == 1:
                return 'succeeded', message
            elif run_status == 0:
                return 'failed', message
            elif run_status == 4:
                return 'running', message
            else:
                return 'unknown', message
        else:
            # Check if job is currently executing
            cursor.execute("""
                SELECT ja.start_execution_date
                FROM msdb.dbo.sysjobactivity ja
                INNER JOIN msdb.dbo.sysjobs j ON ja.job_id = j.job_id
                WHERE j.name = ?
                AND ja.start_execution_date IS NOT NULL
                AND ja.stop_execution_date IS NULL
            """, job_name)

            if cursor.fetchone():
                return 'running', ''

        return 'unknown', ''

    finally:
        cursor.close()
        conn.close()


def monitor_job_and_notify(job_name, file_paths, email_to, notify_on_success, notify_on_failure):
    """
    Monitor a job and send email notifications based on status.
    This runs in a separate thread.
    """
    max_wait_time = 3600  # 1 hour max
    check_interval = 5  # Check every 5 seconds
    elapsed_time = 0

    while elapsed_time < max_wait_time:
        time.sleep(check_interval)
        elapsed_time += check_interval

        status, message = get_job_status(job_name)

        if status == 'succeeded':
            if notify_on_success and email_to:
                send_email(
                    to_email=email_to,
                    subject=f"SQL Job Succeeded: {job_name}",
                    body=f"""
                    <html>
                    <body>
                        <h2>SQL Job Completed Successfully</h2>
                        <p><strong>Job Name:</strong> {job_name}</p>
                        <p><strong>Files Executed:</strong></p>
                        <ul>
                            {''.join([f'<li>{fp}</li>' for fp in file_paths])}
                        </ul>
                        <p><strong>Status:</strong> Success</p>
                        <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                    </body>
                    </html>
                    """
                )
            break

        elif status == 'failed':
            if notify_on_failure and email_to:
                send_email(
                    to_email=email_to,
                    subject=f"SQL Job Failed: {job_name}",
                    body=f"""
                    <html>
                    <body>
                        <h2>SQL Job Failed</h2>
                        <p><strong>Job Name:</strong> {job_name}</p>
                        <p><strong>Files Executed:</strong></p>
                        <ul>
                            {''.join([f'<li>{fp}</li>' for fp in file_paths])}
                        </ul>
                        <p><strong>Status:</strong> Failed</p>
                        <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                        <p><strong>Error Message:</strong></p>
                        <pre>{message}</pre>
                    </body>
                    </html>
                    """
                )
            break


def send_email(to_email, subject, body):
    """Send an email notification."""
    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = SMTP_FROM_EMAIL
        msg['To'] = to_email
        msg['Subject'] = subject

        msg.attach(MIMEText(body, 'html'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)

        print(f"Email sent to {to_email}")

    except Exception as e:
        print(f"Failed to send email: {str(e)}")


@app.route('/')
def index():
    """Render the main page."""
    return render_template('index.html')


@app.route('/api/files')
def get_files():
    """API endpoint to get file tree."""
    pattern = request.args.get('pattern', '')
    root_path = NETWORK_DRIVE_ROOT

    if not root_path or not os.path.exists(root_path):
        return jsonify({'error': 'Network drive path not configured or not accessible'}), 400

    tree = scan_directory_for_sql(root_path, pattern)

    if tree:
        return jsonify(tree)
    else:
        return jsonify({'error': 'No SQL files found'}), 404


@app.route('/api/execute', methods=['POST'])
def execute_files():
    """API endpoint to execute selected SQL files."""
    data = request.json
    file_paths = data.get('files', [])
    email_to = data.get('email_to', '')
    notify_on_success = data.get('notify_on_success', False)
    notify_on_failure = data.get('notify_on_failure', False)

    if not file_paths:
        return jsonify({'error': 'No files selected'}), 400

    try:
        # Create a single job for all files
        # In a more complex scenario, you might create separate jobs
        # For now, we'll create one job per file
        jobs_created = []

        for file_path in file_paths:
            job_id, job_name = create_agent_job(file_path)
            jobs_created.append({'job_id': job_id, 'job_name': job_name, 'file': file_path})

            # Start monitoring thread if email notifications are enabled
            if email_to and (notify_on_success or notify_on_failure):
                thread = threading.Thread(
                    target=monitor_job_and_notify,
                    args=(job_name, [file_path], email_to, notify_on_success, notify_on_failure)
                )
                thread.daemon = True
                thread.start()

        return jsonify({
            'success': True,
            'message': f'Created {len(jobs_created)} job(s)',
            'jobs': jobs_created
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/job-status/<job_name>')
def check_job_status(job_name):
    """API endpoint to check job status."""
    try:
        status, message = get_job_status(job_name)
        return jsonify({'status': status, 'message': message})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    )
