# 🚢 Shipment Tracker

A multi-carrier shipment tracking tool built with Python and Selenium WebDriver. Automatically scrapes tracking data from major shipping lines and exports results to Excel.

## Supported Carriers
- KMTC
- HAPAG-Lloyd
- CMA CGM
- COSCO
- Interasia
- Maersk

## Features
- Automated web scraping via Selenium
- Multi-BL batch tracking
- Excel report export
- Error handling & retry logic

## Setup

### Prerequisites
- Python 3.x
- Chrome browser + ChromeDriver

### Installation

git clone https://github.com/Kamalhutl/shipment-tracker.git
cd shipment-tracker
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

### Run

python main.py

## Project Structure

shipment-tracker/
├── main.py
├── scrapers/
│   ├── kmtc.py
│   ├── hapag.py
│   ├── cma.py
│   ├── cosco.py
│   ├── interasia.py
│   └── maersk.py
├── tracking_2.xlsx
└── README.md

## Output

Results exported to tracking_2.xlsx with the following columns:

| Column | Description |
|--------|-------------|
| BL Number | Bill of Lading number |
| POL | Port of Loading |
| POD | Port of Discharge |
| Container No | Container number |
| Vessel | Vessel name |
| ATA | Actual Time of Arrival |
| FND | Final Destination |
| Latest Status | Current shipment status |

## Requirements

selenium
openpyxl
requests
webdriver-manager

## Notes
- Make sure ChromeDriver version matches your installed Chrome version
- BL numbers are read from tracking_2.xlsx input sheet
- Each carrier has its own scraper module under /scrapers

## License
MIT
x