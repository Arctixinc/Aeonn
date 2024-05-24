import requests
import time
import hashlib
import logging
from datetime import datetime
from pytz import timezone
import os
import zipfile
import shutil
from io import BytesIO
import base64

class RepoMonitor:
    TEMP_DIR = 'temp/'
    CHECK_INTERVAL = 60  # seconds

    def __init__(self, repo_info):
        self.repo_url = repo_info["repo_url"]
        self.telegram_bot_token = repo_info["telegram_bot_token"]
        self.telegram_chat_id = repo_info["telegram_chat_id"]
        self.github_token = repo_info["github_token"]
        self.repo_owner_repo = self.repo_url.replace("https://github.com/", "")
        self.last_status = None
        self.last_sent_time = time.time()
        self.initial_zip_sent = False
        self.repo_name = self.get_repo_name()
        self.initial_branch_files = {}
        self.branch_files = {}

        self.logger = self.configure_logger()
        self.ist = timezone('Asia/Kolkata')
        self.send_telegram_message(f"Hi! I'm now monitoring the repository: {self.repo_url}")
        self.capture_initial_state()

    def configure_logger(self):
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)
        formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] - %(message)s - [%(filename)s, %(lineno)d]')
        formatter.converter = lambda *args: datetime.now(self.ist).timetuple()
        formatter.default_time_format = '%Y-%m-%d %I:%M:%S %p'
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        return logger

    def send_telegram_message(self, message):
        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
        data = {"chat_id": self.telegram_chat_id, "text": message}
        response = requests.post(url, data=data)
        if response.status_code != 200:
            self.logger.error(f"Failed to send message: {response.status_code}, {response.text}")

    def send_telegram_document(self, document_path, caption=""):
        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendDocument"
        with open(document_path, 'rb') as document:
            data = {"chat_id": self.telegram_chat_id, "caption": caption}
            files = {"document": document}
            response = requests.post(url, data=data, files=files)
        if response.status_code != 200:
            self.logger.error(f"Failed to send document: {response.status_code}, {response.text}")

    def is_repo_public(self):
        return requests.get(self.repo_url).status_code == 200

    def get_repo_name(self):
        parts = self.repo_url.split("/")
        return f"{parts[-2]}/{parts[-1]}"

    def get_github_api_response(self, api_url):
        headers = {'Authorization': f'token {self.github_token}'}
        response = requests.get(api_url, headers=headers)
        if response.status_code != 200:
            self.logger.error(f"GitHub API error: {response.status_code}, {response.text}")
            return None
        return response.json()

    def get_repo_content_hash(self):
        api_url = f"https://api.github.com/repos/{self.repo_owner_repo}/contents"
        content = self.get_github_api_response(api_url)
        return hashlib.md5(str(content).encode('utf-8')).hexdigest() if content else None

    def get_repo_branches(self):
        api_url = f"https://api.github.com/repos/{self.repo_owner_repo}/branches"
        return [branch['name'] for branch in self.get_github_api_response(api_url)] if self.get_github_api_response(api_url) else None

    def download_branch_zip(self, branch_name):
        branch_zip_url = f"https://api.github.com/repos/{self.repo_owner_repo}/zipball/{branch_name}"
        headers = {'Authorization': f'token {self.github_token}'}
        response = requests.get(branch_zip_url, headers=headers)
        if response.status_code == 200:
            if not os.path.exists(self.TEMP_DIR):
                os.makedirs(self.TEMP_DIR)
            file_path = f'{self.TEMP_DIR}{branch_name}.zip'
            with open(file_path, 'wb') as f:
                f.write(response.content)
            return file_path
        self.logger.error(f"Failed to download branch zip: {response.status_code}, {response.text}")
        return None

    def create_all_branch_zip(self, branches):
        zip_file_path = f'{self.TEMP_DIR}all_branches.zip'
        with zipfile.ZipFile(zip_file_path, 'w') as zip_file:
            for branch in branches:
                branch_zip = self.download_branch_zip(branch)
                if branch_zip:
                    zip_file.write(branch_zip, os.path.basename(branch_zip))
                    os.remove(branch_zip)
        return zip_file_path

    def get_branch_file_list(self, branch):
        branch_url = f"https://api.github.com/repos/{self.repo_owner_repo}/branches/{branch}"
        branch_data = self.get_github_api_response(branch_url)
        if not branch_data:
            return None

        commit_sha = branch_data['commit']['sha']
        commit_url = f"https://api.github.com/repos/{self.repo_owner_repo}/git/trees/{commit_sha}?recursive=1"
        commit_data = self.get_github_api_response(commit_url)
        if not commit_data:
            return None

        return {file['path']: file['sha'] for file in commit_data['tree'] if file['type'] == 'blob'}

    def capture_initial_state(self):
        branches = self.get_repo_branches()
        if branches:
            for branch in branches:
                self.initial_branch_files[branch] = self.get_branch_file_list(branch)
                self.branch_files[branch] = self.initial_branch_files[branch].copy()

    def check_for_changes(self, branch):
        current_files = self.get_branch_file_list(branch)
        if current_files is None:
            return {}  # Error occurred

        changes = {}
        last_files = self.branch_files.get(branch, {})

        for file_path, sha in current_files.items():
            if file_path not in last_files or last_files[file_path] != sha:
                changes[file_path] = "New file added." if file_path not in last_files else "File modified."

        for file_path in last_files:
            if file_path not in current_files:
                changes[file_path] = "File deleted."

        self.branch_files[branch] = current_files
        return changes if changes else {}  # Return empty dictionary if no changes

    def monitor(self):
        self.last_status = self.is_repo_public()
        if self.last_status is None:
            self.logger.error("Failed to determine repository status during initial check.")
            return

        initial_status_str = 'Public' if self.last_status else 'Private'
        self.send_telegram_message(f"The repository '{self.repo_name}' is currently {initial_status_str} at {self.current_time_ist()}.")

        self.capture_initial_state()  # Capture the initial state of the repository

        if self.last_status and not self.initial_zip_sent:
            self.send_initial_zip()

        while True:
            try:
                self.check_repo_status()
            except Exception as e:
                self.logger.error(f"An error occurred: {e}")
                time.sleep(5)

    def check_repo_status(self):
        current_status = self.is_repo_public()
        if current_status is None:
            self.logger.error("Failed to determine repository status during periodic check.")
            return

        current_time = time.time()
        status_str = 'Public' if current_status else 'Private'
        self.logger.info(f"Repo '{self.repo_name}' status: {status_str} at {self.current_time_ist()}")

        if current_status != self.last_status:
            self.handle_status_change(current_status)
        elif current_time - self.last_sent_time >= 5 * 60:
            self.send_status_update(current_status)

        if current_status:
            self.check_for_repo_changes()

    def handle_status_change(self, current_status):
        status_str = 'Public' if current_status else 'Private'
        if current_status:
            self.send_initial_zip()
            self.capture_initial_state()  # Capture the initial state since the repository is now public
        else:
            self.send_telegram_message(f"The repository '{self.repo_name}' is now private at {self.current_time_ist()}.")
        self.last_status = current_status
        self.last_sent_time = time.time()

    def send_initial_zip(self):
        branches = self.get_repo_branches()
        if branches:
            zip_file_path = self.create_all_branch_zip(branches)
            if zip_file_path:
                self.send_telegram_document(zip_file_path, f"Initial upload of all branches for '{self.repo_name}' at {self.current_time_ist()}")
                os.remove(zip_file_path)
                self.initial_zip_sent = True

    def send_status_update(self, current_status):
        status_str = 'Public' if current_status else 'Private'
        self.send_telegram_message(f"The repository '{self.repo_name}' is still {status_str.lower()} at {self.current_time_ist()}.")
        self.last_sent_time = time.time()

    def check_for_repo_changes(self):
        branches = self.get_repo_branches()
        if branches:
            for branch in branches:
                changes = self.check_for_changes(branch)
                if changes:
                    self.logger.info(f"Changes detected in branch '{branch}' of repository '{self.repo_name}' at {self.current_time_ist()}:")
                    for file_path, change_type in changes.items():
                        message = f"Change detected in branch '{branch}' of repository '{self.repo_name}' at {self.current_time_ist()}:\n{file_path} - {change_type}"
                        self.send_telegram_message(message)
                        self.logger.info(message)

                    branch_zip = self.download_branch_zip(branch)
                    if branch_zip:
                        # Extract and upload zip to GitHub before sending to Telegram
                        self.upload_zip_to_github(branch_zip, branch)
                        self.send_telegram_document(branch_zip, f"Updated branch '{branch}' of repository '{self.repo_name}' at {self.current_time_ist()}")
                        os.remove(branch_zip)
                    else:
                        self.logger.error(f"Failed to download zip file for branch '{branch}'")

    def upload_zip_to_github(self, zip_file_path, branch_name):
        extract_dir = os.path.join(self.TEMP_DIR, branch_name)
        os.makedirs(extract_dir, exist_ok=True)
        
        # Extract the zip file
        with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        
        # Check if branch exists, if not create it
        branches = self.get_repo_branches()
        if branch_name not in branches:
            branch_name = "main" if "main" in branches else "master"
            if branch_name not in branches:
                self.create_branch(branch_name)
        
        # Upload extracted files to GitHub
        for root, _, files in os.walk(extract_dir):
            for file in files:
                file_path = os.path.join(root, file)
                relative_path = os.path.relpath(file_path, extract_dir)
                self.upload_file_to_github(file_path, relative_path, branch_name)
        
        # Clean up extracted directory
        shutil.rmtree(extract_dir)

    def create_branch(self, branch_name):
        api_url = f"https://api.github.com/repos/{self.repo_owner_repo}/git/refs"
        data = {
            "ref": f"refs/heads/{branch_name}",
            "sha": self.get_latest_commit_sha()
        }
        response = requests.post(api_url, headers=self.get_github_headers(), json=data)
        if response.status_code != 201:
            self.logger.error(f"Failed to create branch: {response.status_code}, {response.text}")

    def get_latest_commit_sha(self):
        api_url = f"https://api.github.com/repos/{self.repo_owner_repo}/commits"
        commits = self.get_github_api_response(api_url)
        if commits and len(commits) > 0:
            return commits[0]['sha']
        return None

    def upload_file_to_github(self, file_path, relative_path, branch_name):
        api_url = f"https://api.github.com/repos/Arctixinc/push/contents/{relative_path}"
        with open(file_path, 'rb') as file:
            content = file.read()
        encoded_content = base64.b64encode(content).decode('utf-8')
        data = {
            "message": f"Upload {relative_path} to {branch_name}",
            "content": encoded_content,
            "branch": branch_name
        }
        response = requests.put(api_url, headers=self.get_github_headers(), json=data)
        if response.status_code != 201:
            self.logger.error(f"Failed to upload file: {response.status_code}, {response.text}")

    def get_github_headers(self):
        return {'Authorization': f'token {self.github_token}'}

    def current_time_ist(self):
        return datetime.now(self.ist).strftime('%Y-%m-%d %I:%M:%S %p')
