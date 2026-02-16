import pandas as pd
import time
import os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

def scrape_unistellar_table():
    url = "https://alerts.unistellaroptics.com/transient/events.html"
    
    # Setup Chrome options
    options = Options()
    options.add_argument("--headless")  # Run without opening a window
    options.add_argument("--no-sandbox") # Required for server environments
    options.add_argument("--disable-dev-shm-usage") # Overcome limited resource problems
    options.add_argument("--window-size=1920,1080")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    # Check for system-installed chromedriver (Docker / Streamlit Cloud)
    system_driver_path = "/usr/bin/chromedriver"
    if os.path.exists(system_driver_path):
        service = Service(system_driver_path)
    else:
        # Local fallback
        service = Service(ChromeDriverManager().install())

    driver = webdriver.Chrome(service=service, options=options)

    try:
        print("Connecting to Unistellar Alerts...")
        driver.get(url)

        # Wait up to 15 seconds for the table body to contain at least one row
        wait = WebDriverWait(driver, 15)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))
        
        # Brief pause to ensure all React elements are rendered
        time.sleep(2)

        # Get headers
        header_elements = driver.find_elements(By.TAG_NAME, "th")
        headers = [h.text.strip().replace('\n', ' ') for h in header_elements]
        if not headers[0]: headers[0] = "DeepLink"

        # Get all rows
        rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
        data = []

        print(f"Found {len(rows)} targets. Extracting data...")

        for row in rows:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) < 2: continue
            
            row_data = []
            for i, cell in enumerate(cells):
                if i == 0: # Handle the deep link icon
                    try:
                        link = cell.find_element(By.TAG_NAME, "a").get_attribute("href")
                        row_data.append(link)
                    except:
                        row_data.append("")
                else:
                    row_data.append(cell.text.strip())
            data.append(row_data)

        # Create DataFrame
        df = pd.DataFrame(data, columns=headers)
        
        print("\nSuccess! Sample of scraped data:")
        print(df.head(3))
        print(f"\nExtracted {len(df)} rows.")
        return df

    except Exception as e:
        print(f"An error occurred: {e}")
        return None
    finally:
        driver.quit()

if __name__ == "__main__":
    scrape_unistellar_table()