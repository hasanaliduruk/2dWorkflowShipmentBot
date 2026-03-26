# 2DWorkflow Automated Shipment Bot 📦

A robust, background-running automation and logistics tracking bot designed for the 2DWorkflow platform. This tool programmatically analyzes shipment drafts, filters them based on dynamic mileage thresholds and target warehouses (e.g., AVP1, TEB3), and automates operational workflows using a multi-account context-switching architecture.

## 🚀 Key Features

- **Multi-Account Context Switching:** Seamlessly manages and switches between multiple 2DWorkflow accounts dynamically during scheduled execution loops without dropping sessions.
- **Advanced Task Scheduling:** Implements robust asynchronous task scheduling via `APScheduler`. Supports Interval, Half-hourly, and Quarterly execution modes.
- **Smart Logistics Filtering:** Programmatically evaluates shipment drafts against user-defined Maximum Mileage limits and specific target warehouses.
- **Stateless & Secure Authentication:** User credentials are not persisted in any database. The system handles authentication and token lifecycle directly in RAM via `requests.Session()`.
- **Live Dashboard & Data Monitoring:** Built with `Streamlit` and `Pandas` to provide a real-time tracking interface, live logging, and historical data extraction.
- **Fault Tolerance:** Includes internal crash handlers and Teams Webhook integration for immediate failure/success notifications.

## 🛠️ Tech Stack

- **Core:** Python 3.10+
- **Frontend / GUI:** Streamlit
- **Task Scheduling:** APScheduler
- **Data Manipulation:** Pandas
- **Network & Auth:** Requests, BeautifulSoup4

## 📦 Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/hasanaliduruk/2dWorkflowShipmentBot.git](https://github.com/hasanaliduruk/2dWorkflowShipmentBot.git)
   cd 2dWorkflowShipmentBot
   ```

2. **Install the required dependencies:**
   It is recommended to use a virtual environment.
   ```bash
   pip install -r requirements.txt
   ```

3. **Environment Configuration (Optional):**
   To enable Microsoft Teams Webhook notifications, create a `.streamlit/secrets.toml` file in the root directory:
   ```toml
   TEAMS_WEBHOOK = "https://your-webhook-url-here"
   ```

## ⚙️ Usage

Start the web application server by running:
```bash
streamlit run app.py
```
The application will be accessible at `http://localhost:8501`. Log in using your 2DWorkflow credentials to initiate the background task loops.

## 📝 Disclaimer
This architecture was initially developed to optimize and automate operational workflows for e-commerce logistics. It demonstrates advanced concepts like Session Replay, undocumented API routing, and state management. Ensure you comply with the platform's Terms of Service before deploying in a production environment.

---
*Developed by [Hasan Ali Duruk](https://github.com/hasanaliduruk).*
