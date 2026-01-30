from collections import deque
from datetime import datetime
import requests
from apscheduler.schedulers.background import BackgroundScheduler
import pandas as pd

from bot.constants import USER_AGENT
from bot.scheduler import gorev

class GlobalManager:
    def __init__(self, email, password, teams_webhook_url=None):
        # 1. Credentials (Stored only in RAM for this session)
        self.email = email
        self.password = password
        self.teams_webhook_url = teams_webhook_url
        
        # 2. User-Specific Data
        # Structure: { "01.30.2026 14:00": { 'name':..., 'loc':... } }
        self.watch_list = {}
        self.logs = deque(maxlen=50)
        self.history = deque(maxlen=50)
        self.mile_threshold = 300

        # Scheduling settings
        self.mins_threshold = 30
        self.scheduler_mode = "interval"
        self.is_running = False 
        
        # 3. Isolated Session
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
        })
        self.available_accounts = [] 
        self.current_account_name = "Bilinmiyor"
        self.current_account_id = None

        # 4. User-Specific Scheduler
        self.scheduler = BackgroundScheduler()
        self.scheduler.start()

    def add_log(self, message, type="info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        icon_map = {"success": "✅", "error": "❌", "warning": "⚠️", "info": "ℹ️"}
        icon = icon_map.get(type, "ℹ️")
        self.logs.appendleft(f"{timestamp} {icon} {message}")

    def start_bot_process(self):
        """Starts or Reschedules the job based on the selected mode"""
        
        # 1. Determine Trigger Type
        if self.scheduler_mode == "half_hourly":
            # Run at :00 and :30
            trigger_args = {'trigger': 'cron', 'minute': '0,30'}
            log_msg = "Mod: Saat Başı ve Buçuk (xx:00, xx:30)"
            
        elif self.scheduler_mode == "quarterly":
            # Run at :00, :15, :30, :45
            trigger_args = {'trigger': 'cron', 'minute': '0,15,30,45'}
            log_msg = "Mod: Çeyrek Saatler (xx:00, xx:15...)"
            
        else:
            # Default: Interval
            trigger_args = {'trigger': 'interval', 'minutes': self.mins_threshold}
            log_msg = f"Mod: Her {self.mins_threshold} dakikada bir"

        # 2. Add or Reschedule
        if not self.scheduler.get_job('user_task'):
            self.scheduler.add_job(
                gorev, 
                id='user_task', 
                args=[self], 
                max_instances=1,
                **trigger_args
            )
        else:
            self.scheduler.reschedule_job('user_task', **trigger_args)
            
        # Optional: Log the change internally if needed (mostly for debugging)
        print(f"Scheduler updated: {log_msg}")

    def stop_bot_process(self):
        if self.scheduler.get_job('user_task'):
            self.scheduler.remove_job('user_task')
            
    def update_watch_list_from_df(self, df_records):
        new_watch_list = {}
        for item in df_records:
            key = item['date']
            final_item = item.copy()
            
            if key in self.watch_list:
                existing = self.watch_list[key]
                final_item['found_warehouses'] = existing.get('found_warehouses', [])
                final_item['account_id'] = existing.get('account_id')
                final_item['account_name'] = existing.get('account_name')
            else:
                if 'found_warehouses' not in final_item:
                    final_item['found_warehouses'] = []

            new_watch_list[key] = final_item
        
        self.watch_list = new_watch_list

    def get_watch_list_df(self):
        """
        Converts Dictionary -> DataFrame for the UI
        """
        if not self.watch_list:
            return pd.DataFrame()
        return pd.DataFrame(list(self.watch_list.values()))
    
    def add_history_entry(self, draft_name, found_data, account_name):
        """
        Records a success. Handles list of dicts: [{'AVP1': 150}, {'MEM1': 200}]
        """
        timestamp = datetime.now().strftime("%H:%M")
        formatted_list = []
        for item in found_data:
            if isinstance(item, dict):
                for k, v in item.items():
                    formatted_list.append(f"{k}: {v} Mil")
            elif isinstance(item, str):
                formatted_list.append(item)
        entry = {
            "account": account_name,
            "name": draft_name,
            "found": ", ".join(formatted_list),
            "time": timestamp
        }
        self.history.appendleft(entry)