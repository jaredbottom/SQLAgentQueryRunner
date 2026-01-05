import os
import pyodbc
import uuid
from datetime import datetime
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


def create_or_get_operator(email_address):
    """
    Create a temporary operator for the email address or get existing one.
    Returns the operator name.
    """
    # Create a unique but consistent operator name based on email
    operator_name = f"TempOp_{email_address.replace('@', '_at_').replace('.', '_')}"[:128]

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Check if operator exists
        cursor.execute("""
            SELECT name FROM msdb.dbo.sysoperators WHERE name = ?
        """, operator_name)

        if cursor.fetchone():
            # Operator exists, update email if needed
            cursor.execute("""
                EXEC msdb.dbo.sp_update_operator
                    @name = ?,
                    @enabled = 1,
                    @email_address = ?
            """, operator_name, email_address)
        else:
            # Create new operator
            cursor.execute("""
                EXEC msdb.dbo.sp_add_operator
                    @name = ?,
                    @enabled = 1,
                    @email_address = ?
            """, operator_name, email_address)

        conn.commit()
        return operator_name

    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def create_agent_job(file_path, email_to=None, notify_on_success=False, notify_on_failure=False, job_name=None):
    """
    Create a SQL Server Agent job to execute a SQL file.
    Optionally configure email notifications via SQL Server Agent operators.
    Returns the job_id and job_name.
    """
    if not job_name:
        job_name = f"RunSQLFile_{uuid.uuid4().hex[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    job_id = str(uuid.uuid4())
    operator_name = None

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Create operator if email notifications are requested
        if email_to and (notify_on_success or notify_on_failure):
            operator_name = create_or_get_operator(email_to)

        # Create the job
        cursor.execute("""
            EXEC msdb.dbo.sp_add_job
                @job_name = ?,
                @enabled = 1,
                @description = ?,
                @delete_level = 1,
                @job_id = ? OUTPUT
        """, job_name, f'Auto-generated job to run SQL file: {file_path}', job_id)

        # Add job step with email notifications on failure at step level
        cursor.execute("""
            EXEC msdb.dbo.sp_add_jobstep
                @job_name = ?,
                @step_name = 'Execute SQL File',
                @subsystem = 'TSQL',
                @command = ?,
                @retry_attempts = 0,
                @retry_interval = 0,
                @on_success_action = 1,
                @on_fail_action = 2
        """, job_name, f"EXEC run_query_file '{file_path}'")

        # Add job to local server
        cursor.execute("""
            EXEC msdb.dbo.sp_add_jobserver
                @job_name = ?,
                @server_name = '(local)'
        """, job_name)

        # Add notification settings if operator is configured
        if operator_name:
            notify_level_success = 1 if notify_on_success else 0  # 1 = When the job succeeds
            notify_level_failure = 2 if notify_on_failure else 0  # 2 = When the job fails

            # Combine notification levels (0=Never, 1=Success, 2=Failure, 3=Always)
            if notify_on_success and notify_on_failure:
                notify_level = 3
            elif notify_on_success:
                notify_level = 1
            elif notify_on_failure:
                notify_level = 2
            else:
                notify_level = 0

            if notify_level > 0:
                cursor.execute("""
                    EXEC msdb.dbo.sp_update_job
                        @job_name = ?,
                        @notify_level_email = ?,
                        @notify_email_operator_name = ?
                """, job_name, notify_level, operator_name)

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
    email_to = data.get('email_to', '').strip()
    notify_on_success = data.get('notify_on_success', False)
    notify_on_failure = data.get('notify_on_failure', False)

    if not file_paths:
        return jsonify({'error': 'No files selected'}), 400

    try:
        jobs_created = []

        for file_path in file_paths:
            # Create job with email notification configuration
            # SQL Server Agent will handle the email notifications natively
            job_id, job_name = create_agent_job(
                file_path=file_path,
                email_to=email_to if email_to else None,
                notify_on_success=notify_on_success,
                notify_on_failure=notify_on_failure
            )
            jobs_created.append({'job_id': job_id, 'job_name': job_name, 'file': file_path})

        notification_msg = ""
        if email_to and (notify_on_success or notify_on_failure):
            notification_msg = f" Email notifications will be sent to {email_to}."

        return jsonify({
            'success': True,
            'message': f'Created {len(jobs_created)} job(s).{notification_msg}',
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
