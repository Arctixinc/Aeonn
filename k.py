import requests
import time
import hashlib
import logging
from datetime import datetime
from pytz import timezone
import os
import zipfile
import subprocess

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
                        self.send_telegram_document(branch_zip, f"Updated branch '{branch}' of repository '{self.repo_name}' at {self.current_time_ist()}")
                        self.upload_to_github(branch, branch_zip)
                        os.remove(branch_zip)
                    else:
                        self.logger.error(f"Failed to download zip file for branch '{branch}'")

    def current_time_ist(self):
        return datetime.now(self.ist).strftime('%Y-%m-%d %I:%M:%S %p')

    def upload_to_github(self, branch, zip_path):
        temp_dir = f"{self.TEMP_DIR}{branch}_extracted"
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)

        extracted_folder = os.path.join(temp_dir, os.listdir(temp_dir)[0])

        # Initialize git repository and add files
        subprocess.run(["git", "init"], cwd=extracted_folder)
        subprocess.run(["git", "remote", "add", "origin", f"https://{self.github_token}@github.com/Arctixinc/push.git"], cwd=extracted_folder)
        subprocess.run(["git", "checkout", "-b", branch], cwd=extracted_folder)
        subprocess.run(["git", "add", "."], cwd=extracted_folder)

        # Exclude certain files or directories like "workflows"
        subprocess.run(["git", "reset", "--", "workflows"], cwd=extracted_folder)
        subprocess.run(["git", "clean", "-fdX"], cwd=extracted_folder)

        commit_message = f"Update branch {branch} with latest changes tracked by RepoMonitor script"
        subprocess.run(["git", "commit", "-m", commit_message], cwd=extracted_folder)

        # Push changes to GitHub
        result = subprocess.run(["git", "push", "origin", branch, "--force"], cwd=extracted_folder, capture_output=True, text=True)
        if result.returncode == 0:
            self.logger.info(f"Successfully pushed changes to branch '{branch}' on GitHub.")
        else:
            self.logger.error(f"Failed to push changes to branch '{branch}' on GitHub: {result.stderr}")

        # Clean up
        try:
            subprocess.run(["rm", "-rf", temp_dir])
        except Exception as e:
            self.logger.error(f"An error occurred while cleaning up: {e}")

        # Remove the zip file
        try:
            os.remove(zip_path)
        except Exception as e:
            self.logger.error(f"An error occurred while removing the zip file: {e}")
