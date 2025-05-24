import sys, os
# Add the project root directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
from datetime import datetime
from utils.logger import setup_logger

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

from database.DBConnector import find_article_by_conditions, add_article, get_topic_df, find_topic_by_conditions


import pandas as pd

logger = setup_logger('scrap', "scrap_articles.log")

N_SEARCH_PAGES_TO_SCRAPE = 3



def init_browser():
    """
    Function to initialize the Chrome browser
    """
    options = Options()
    options.add_argument("--headless=new")  # Новий headless режим
    options.add_argument("--disable-gpu")  # Вимкнути GPU (для стабільності)
    options.add_argument("--no-sandbox")  # Для роботи в Docker
    options.add_argument("--disable-dev-shm-usage")  # Запобігання помилкам у Linux  # Set to True if you don't want the browser window to open
    options.add_argument("--window-size=1920,1080")  # Розмір вікна
    options.add_argument("--disable-extensions")
    options.add_argument("--enable-unsafe-swiftshader")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver

def scrape_articles(url, max_retries=3):
    """
    Function to scrape article content with retry logic and improved stability
    
    Args:
        url: URL of the article to scrape
        max_retries: Maximum number of retry attempts
    
    Returns:
        str: Scraped article content
    """
    content_text = ''
    retries = 0
    
    while retries < max_retries:
        driver = init_browser()
        try:
            logger.debug(f"Fetching article: {url} (Attempt {retries + 1}/{max_retries})")
            driver.get(url)
            
            # Use WebDriverWait instead of fixed sleep
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            
            # Wait for article content to be available
            wait = WebDriverWait(driver, 30)
            content_div = wait.until(EC.presence_of_element_located((By.CLASS_NAME, 'article__content')))
            
            paragraphs = content_div.find_elements(By.TAG_NAME, 'p')
            content_text = ''
            
            for elem in paragraphs:
                content_text += elem.text + " "
            
            # If we got here, scraping was successful
            break
            
        except Exception as e:
            retries += 1
            logger.error(f"Error occurred for link {url} (Attempt {retries}/{max_retries}): {e}")
            if "tab crashed" in str(e).lower():
                logger.warning(f"Tab crash detected, retrying with different options")
                # Consider reducing load on browser in next attempt
            
            if retries >= max_retries:
                logger.error(f"Failed to scrape {url} after {max_retries} attempts")
            else:
                logger.info(f"Retrying in 5 seconds...")
                time.sleep(5)  # Wait before retry
                
        finally:
            # Always close the browser
            try:
                driver.quit()  # Using quit() instead of close() for more thorough cleanup
            except Exception:
                pass
    
    return content_text.strip()


def scrape_article_content(driver, url, topic_id):
    """
    Function to scrape article content from the provided URL
    """

    logger.debug(f"Opening page: {url}")
    
    try:
        driver.get(url)
        time.sleep(10)  # Wait for the page to load

        search_results = driver.find_element(By.CLASS_NAME, "search__results-list")

        # Знайдіть усі дочірні елементи з класом 'card container__item'
        cards = search_results.find_elements(By.CLASS_NAME, "card.container__item")
        
        if not cards:
            logger.info("No articles found on this page")
            driver.close()
            return True  # Return True to break out of loop (no more articles)

        # Обробка кожної картки
        for card in cards:
            try:
                link_element = card.find_element(By.CLASS_NAME, "container__text")
                link_href = card.find_element(By.TAG_NAME, "a").get_attribute("href")

                headline = link_element.find_element(By.CLASS_NAME, "container__headline-text").text
                date = link_element.find_element(By.CLASS_NAME, "container__date").text
                description = link_element.find_element(By.CLASS_NAME, "container__description").text

                # Перевіряємо, чи стаття вже є в базі
                exists_in_db = find_article_by_conditions(title=headline)

                if exists_in_db:
                    logger.warning(f"Article exist in database! Skip. (Link {link_href})")
                    continue  # Skip this article but continue with others

                # Додаємо статтю в базу за допомогою функції add_article
                add_article(
                    title=headline,
                    url=link_href,
                    article_date=datetime.strptime(date, "%b %d, %Y").strftime("%Y-%m-%d"),
                    content=scrape_articles(link_href),
                    fk_topic_id=topic_id
                )

                logger.debug(f"Article adding: {headline} ({date})")

            except Exception as e:
                logger.error(f"--- Error {link_href}: {e} ---")
                continue 

        driver.close()
        return False  # Return False to continue with next page
        
    except Exception as e:
        logger.error(f"Error processing page {url}: {e}")
        driver.close()
        return False  # Continue to next page on error



# Main function
def main():
    logger.info("--- Starting article scraping process... ---")

    topics = get_topic_df(find_topic_by_conditions())

    for _, topic in topics.iterrows():
        for page in range(1, N_SEARCH_PAGES_TO_SCRAPE + 1):  # Scrape first n pages for each topic
            logger.info(f"Scrapping topic: {topic.topic_name} (Page {page})")
            link = f'https://edition.cnn.com/search?q={"+".join(topic.query.split(" "))}&from={(page-1)*30}&size=30&page={page}&sort=newest&types=article&section='
            should_break = scrape_article_content(init_browser(), link, topic.topic_id)
            if should_break:
                logger.info(f"No more articles for topic {topic.topic_name}. Moving to next topic.")
                break
    
    logger.info("--- Scrapping has ended successfully! ---")


if __name__ == "__main__":
    main()