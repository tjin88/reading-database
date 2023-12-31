import datetime
import time
import re
import os
import django
import logging
import traceback
from logging.handlers import RotatingFileHandler
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.db import IntegrityError
from django.core.exceptions import ValidationError
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from centralized_API_backend.models import LightNovelPub

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'django_app/django_app/settings')
django.setup()

def get_next_log_file_name(base_dir, base_filename):
    counter = 0
    while True:
        if counter == 0:
            log_file_name = f"{base_filename}.txt"
        else:
            log_file_name = f"{base_filename}_{counter}.txt"
        
        full_path = os.path.join(base_dir, log_file_name)
        if not os.path.exists(full_path):
            return full_path
        
        counter += 1

# Setting up the logging configuration
log_directory = "../out/LightNovelPub"
log_base_filename = "scrapeLightNovelPub"
log_file_path = get_next_log_file_name(log_directory, log_base_filename)

# Ensure the log directory exists
os.makedirs(log_directory, exist_ok=True)

# Setting up the logging config, storing as a file and outputting to console
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        RotatingFileHandler(log_file_path, maxBytes=10485760, backupCount=5),  # 10MB per file, max 5 files of size 10 MB
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("LightNovelPubScraper")

class LightNovelPubScraper:
    def __init__(self):
        """
        Initializes the LightNovelPubScraper.

        Sets up the Chrome WebDriver with headless options and defines the look-back period for scraping data.
        """
        options = Options()
        options.headless = True
        self.driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        self.LAST_SCRAPPED_DATE = 3 # Looks at the past x days of released books

    def scrape_light_novel_pub(self):
        """
        Scrapes the LightNovelPub website for light novel details and updates the database.

        The function iterates over each book found on the main listing page. For each book, it first checks if it exists in the database.
        If the book does not exist in the database, create the book entry in the database.
        If the book does exist, check if the newest chapter is different:
            - If different, update the book entry in the database
            - If not different, skip processing this book entry
        The function stops if it skips 5 books consecutively, assuming no new updates are available.

        Returns:
        None
        """
        base_url = 'https://lightnovelpub.vip'
        main_url = f'{base_url}/browse/genre-all-25060123/order-updated/status-all'

        updated = 0
        created = 0
        inital_skipped = 0
        post_update_skipped = 0
        ever_updated = False
        errors = 0
        books = []
        try:
            books = self.scrape_main_page(main_url)

            for title, url in books:
                try:
                    self.navigate_to_url(url)

                    # Query based on title only*
                    existing_book = LightNovelPub.objects.filter(title=title).first()

                    if existing_book:
                        newest_chapter = self.scrape_newest_chapter(url)
                        if newest_chapter == existing_book.newest_chapter:
                            # Skip updating if the book has not changed
                            inital_skipped += 1 if not ever_updated else 0
                            post_update_skipped += 1 if ever_updated else 0
                            logger.info(f"Book {updated+created+inital_skipped+post_update_skipped+errors}/{len(books)} - {'Skipped'}: {title}")
                            
                            if post_update_skipped >= 5:
                                break
                            continue
                        
                    # Get all details for the book
                    details = self.scrape_book_details(title, url)

                    # Attempt to update an existing book or create a new one
                    lightnovel, book_created = LightNovelPub.objects.update_or_create(
                        title=details['title'],
                        novel_source=details['novel_source'],
                        defaults=details
                    )
                    if book_created:
                        created += 1
                        ever_updated = True
                    else:
                        updated += 1
                        ever_updated = True
                    logger.info(f"Book {updated+created+inital_skipped+post_update_skipped+errors}/{len(books)} - {'Created' if book_created else 'Updated'}: {title}")
                except WebDriverException as e:
                    logger.error(f"Book {updated+created+inital_skipped+post_update_skipped+errors}/{len(books)} - WebDriverException encountered for {title} at {url}: {e}")
                    errors += 1
                except IntegrityError as e:
                    logger.error(f"Book {updated+created+inital_skipped+post_update_skipped+errors}/{len(books)} - Database integrity error for {title}: {e}")
                    errors += 1
                except ValidationError as e:
                    logger.error(f"Book {updated+created+inital_skipped+post_update_skipped+errors}/{len(books)} - Validation error for {title}: {e}")
                    errors += 1
                except Exception as e:
                    logger.error(f"Book {updated+created+inital_skipped+post_update_skipped+errors}/{len(books)} - Unexpected error while processing book '{title}': {e}")
                    logger.error("Exception traceback: " + traceback.format_exc())
                    errors += 1
        finally:
            if len(books) != 0:
                logger.info(f"Created: {created}/{len(books)}, Updated: {updated}/{len(books)}, Errors: {errors}/{len(books)}, Skipped: {inital_skipped+post_update_skipped}/{len(books)}")
            else:
                logger.info(f"No books were updated. The database is already up-to-date :)")
            logger.info(f"Please see {log_file_path} for the full log details.")
            self.driver.quit()

    def scrape_main_page(self, url):
        """
        Scrapes the main listing page for book URLs using Selenium.
        This method scrapes the main page of light novel pub to find all the books listed. 
        It uses Selenium's WebDriverWait to ensure that the page is loaded before attempting to find elements. 
        This method uses 'is_recent_update' to check if the book update is within the desired timeframe.

        Args:
        url (str): URL of the main listing page.

        Returns:
        list: A list of tuples containing book titles and their URLs.
        """
        self.navigate_to_url(url)
        books = []

        while True:
            # Scrape the current page
            book_elements = self.wait_for_elements(By.CLASS_NAME, 'novel-item')
            for element in book_elements:
                title = self.get_element_text(By.CLASS_NAME, 'novel-title', default_text='Title not available', element=element)
                book_url = self.get_element_attribute(By.TAG_NAME, 'a', 'href', default_value=None, element=self.wait_for_element(By.CLASS_NAME, 'novel-title', element=element))
                update_info = self.get_element_text(By.CSS_SELECTOR, '.novel-stats span', default_text='No update info', element=element)

                # Since this script will be executed ~every 4 hours, 
                # I should only need to check the past 4 hours of books
                # For my sake, I'm checking the past self.LAST_SCRAPPED_DATE days of books
                # (Currently that is 3 days, but it is subject to change)
                if not self.is_recent_update(update_info):
                    return books
                
                books.append((title, book_url))

            # If there are more books to add, add them to the list. If not, return all books.
            next_page_element = self.wait_for_element(By.CLASS_NAME, 'PagedList-skipToNext', timeout=5)

            if next_page_element:
                next_page_url = self.get_element_attribute(By.TAG_NAME, 'a', 'href', default_value=None, element=next_page_element)
                self.navigate_to_url(next_page_url)
            else:
                return books

    def is_recent_update(self, update_info):
        """
        Checks if the book's update information indicates a recent update.

        Args:
            update_info (str): Update information text, e.g., 'x days ago'.

        Returns:
            bool: True if the update is recent, False otherwise.
        """
        if 'DAYS AGO' in update_info:
            days_ago = int(update_info.split(' ')[0])
            return days_ago <= self.LAST_SCRAPPED_DATE
        elif any(string in update_info for string in ['No update info', 'HOURS AGO', 'YESTERDAY']):
            return True
        
        # ** Assuming we scrape at least once per month **
        return False
    
    def scrape_newest_chapter(self, book_url):
        """
        Scrapes only the newest chapter of a book.

        Args:
        book_url (str): URL of the book's detail page.

        Returns:
        str: The newest chapter of the book.
        """
        try:
            return self.get_element_text(By.CSS_SELECTOR, 'nav.content-nav p.latest', 'Chapter not available')
        except NoSuchElementException as e:
            logger.warning(f"Element not found in {book_url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error scraping newest chapter from {book_url}: {e}")
            return None

    def scrape_book_details(self, title, book_url):
        """
        Scrapes detailed information about a book from its individual page using Selenium.
        
        Args:
        title (str): Title of the book.
        book_url (str): URL of the book's detail page.
        
        Returns:
        dict: A dictionary containing key details of the book.
        """
        try:
            synopsis = self.get_element_text(By.CSS_SELECTOR, '.summary .content')
            author = self.get_element_text(By.CSS_SELECTOR, '.author', 'Author not available').replace('Author:', '').strip()
            updated_on = self.get_element_text(By.CSS_SELECTOR, 'nav.content-nav p.update')
            # the below line is throwing the issue***
            newest_chapter = self.get_element_text(By.CSS_SELECTOR, 'nav.content-nav p.latest')
            genres = [genre.text.strip() for genre in self.wait_for_elements(By.CSS_SELECTOR, 'div.categories a')]
            image_url = self.get_element_attribute(By.CSS_SELECTOR, 'figure.cover img', 'src')
            rating = self.get_element_text(By.CSS_SELECTOR, 'div.rating-star strong')
            status = self.get_element_text(By.CSS_SELECTOR, 'div.header-stats span:nth-of-type(4) strong')
            followers = self.get_element_text(By.CSS_SELECTOR, 'div.header-stats span:nth-of-type(3) strong')

            timezone_aware_updated_on = self.parse_relative_date(updated_on)

            chapters = self.extract_chapters(f'{book_url}/chapters')

            book_details = {
                'title': title,
                'synopsis': synopsis,
                'author': author,
                # 'artist': "None",
                # 'released_by': "None",
                # 'serialization': "None",
                # 'posted_by': "None",
                # The following two fields need to be both "datetime" fields
                # 'posted_on': "placeholder to be found", # TODO: Find the original data posted
                'updated_on': timezone_aware_updated_on,
                'newest_chapter': newest_chapter,
                'genres': genres,
                'image_url': image_url,
                'rating': rating,
                'status': status,
                'novel_type': 'Light Novel',
                'novel_source': 'Light Novel Pub',
                'followers': followers,
                'chapters': chapters
            }

            return book_details
        except NoSuchElementException as e:
            logger.error(f"Element not found in {book_url}: {e}")
        except WebDriverException as e:
            logger.error(f"WebDriverException encountered for {title} at {book_url}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error while processing {title}: {e}")
    
    def navigate_to_url(self, url):
        """
        Navigates to a specified URL using the WebDriver.

        Args:
            url (str): The URL to navigate to.
        """
        try:
            self.driver.get(url)
        except WebDriverException as e:
            logger.error(f"Error navigating to URL {url}: {e}")
            raise
    
    def wait_for_elements(self, by, value, timeout=10):
        """
        Waits for multiple elements to be present on the page.

        Args:
            by (By): The Selenium By strategy.
            value (str): The value to locate the elements.
            timeout (int): Maximum time to wait for the elements. Default is 10 seconds.

        Returns:
            List[WebElement]: A list of found elements, or an empty list if none found within the timeout.
        """
        try:
            return WebDriverWait(self.driver, timeout).until(
                EC.presence_of_all_elements_located((by, value))
            )
        except (TimeoutException, WebDriverException) as e:
            logger.error(f"Error waiting for elements {value}: {e}")
            return []

    def wait_for_element(self, by, value, timeout=10, element=None):
        """
        Waits for a specific element to be present on the page or within a parent element.

        Args:
        by (By): The Selenium By strategy.
        value (str): The value to locate the element.
        timeout (int): Maximum time to wait for the element. Default is 10 seconds.
        element (WebElement, optional): The parent element to search within. Default is None, which means search in the entire page.

        Returns:
        WebElement: The found element, or None if not found within the timeout.
        """
        try:
            target = element if element else self.driver
            return WebDriverWait(target, timeout).until(
                EC.presence_of_element_located((by, value))
            )
        except (TimeoutException, WebDriverException) as e:
            if value != "PagedList-skipToNext":
                logger.error(f"Error waiting for element {value}: {e}")
                raise
            else:
                # logger.info(f"Starting process to update books")
                # Time to update books!
                return None
    
    def get_element_text(self, by, value, default_text='Not Available', element=None):
        """
        Waits for an element to be present on the page and retrieves its text.

        Args:
        by (By): The Selenium By strategy.
        value (str): The value to locate the element.
        default_text (str): Default text to return if the element is not found.
        element (WebElement, optional): The parent element to search within. Default is None, which means search in the entire page.

        Returns:
        str: The text of the found element or default text if not found.
        """
        try:
            target_element = element if element else self.driver
            element = self.wait_for_element(by, value, element=target_element)
            return element.text.strip() if element else default_text
        except (TimeoutException, WebDriverException):
            logger.warning(f"Element {value} not found, using default value.")
            return default_text

    def get_element_attribute(self, by, value, attribute, default_value=None, element=None):
        """
        Waits for an element to be present on the page and retrieves a specified attribute.

        Args:
        by (By): The Selenium By strategy.
        value (str): The value to locate the element.
        attribute (str): The attribute to retrieve from the element.
        default_value (any): Default value to return if the element or attribute is not found.
        element (WebElement, optional): The parent element to search within. Default is None, which means search in the entire page.

        Returns:
        str: The value of the attribute or default value if not found.
        """
        try:
            target_element = element if element else self.driver
            element = self.wait_for_element(by, value, element=target_element)
            return element.get_attribute(attribute) if element and element.get_attribute(attribute) else default_value
        except (TimeoutException, WebDriverException):
            logger.warning(f"Element {value} not found, using default value.")
            return default_value

    def extract_chapters(self, chapters_url):
        """
        Extracts chapter details from a given URL.

        Args:
            chapters_url (str): The URL to scrape chapters from.

        Returns:
            dict: A dictionary where each key is a chapter title and each value is the corresponding chapter link.
        """
        self.navigate_to_url(chapters_url)

        book_chapters = {}
        try:
            while True:
                chapter_elements = self.wait_for_elements(By.CSS_SELECTOR, 'ul.chapter-list a')

                for chapter in chapter_elements:                
                    chapter_title, chapter_link = self.process_chapter_element(chapter)
                    # logger.info(f"Extracted: {chapter_title} - {chapter_link}")
                    book_chapters[chapter_title] = chapter_link
                
                # If there are more books to add, add them to the list. If not, return all books.
                next_page_element = self.wait_for_element(By.CLASS_NAME, 'PagedList-skipToNext', timeout=5)
                
                # logger.info(f"book_chapters: {book_chapters}")

                if next_page_element:
                    next_page_url = self.get_element_attribute(By.TAG_NAME, 'a', 'href', default_value=None, element=next_page_element)
                    self.navigate_to_url(next_page_url)
                else:
                    # No more pages found. Push to database
                    break
        except Exception as e:
            logger.error(f"An error occurred: {e}")

        logger.info(f'Returning {len(book_chapters)} chapters!')
        return book_chapters

    def process_chapter_element(self, chapter_element):
        """
        Processes a single chapter element to extract its title and link.

        Args:
            chapter_element (WebElement): The web element representing a chapter.

        Returns:
            tuple: A tuple containing the chapter title and link.
        """
        try:
            number = self.get_element_text(By.CLASS_NAME, 'chapter-no', default_text='', element=chapter_element)
            title = self.get_element_text(By.CLASS_NAME, 'chapter-title', default_text='', element=chapter_element)
            chapter_title = f'{number} - {title}' if number else title
            
            chapter_link = chapter_element.get_attribute('href')
            return chapter_title, chapter_link
        except Exception as e:
            logger.error(f"Error processing chapter element: {e}")
            return None, None

    # def wait_for_element(self, by, value, timeout=10, element=None):
    #     """
    #     Waits for a specific element to be present on the page or within a parent element.

    #     Args:
    #     by (By): The Selenium By strategy.
    #     value (str): The value to locate the element.
    #     timeout (int): Maximum time to wait for the element. Default is 10 seconds.
    #     element (WebElement, optional): The parent element to search within. Default is None, which means search in the entire page.

    #     Returns:
    #     WebElement: The found element, or None if not found within the timeout.
    #     """
    #     try:
    #         target = element if element else self.driver
    #         return WebDriverWait(target, timeout).until(
    #             EC.presence_of_element_located((by, value))
    #         )
    #     except (TimeoutException, WebDriverException) as e:
    #         if value != "PagedList-skipToNext":
    #             logger.error(f"Error waiting for element {value}: {e}")
    #             raise
    #         else:
    #             logger.info(f"Starting process to update books")
    #             return None
    
    @staticmethod
    def parse_relative_date(time_str):
        """
        Parses a relative date string into a timezone-aware datetime object.

        Args:
            time_str (str): A string representing a relative date (e.g., 'x days ago').

        Returns:
            datetime: A timezone-aware datetime object representing the parsed date.
        """
        today = timezone.now()
        
        if time_str == "Updated yesterday":
            return today - datetime.timedelta(days=1)
        elif "days ago" in time_str:
            days = int(re.search(r'(\d+) days ago', time_str).group(1))
            return today - datetime.timedelta(days=days)
        elif "years ago" in time_str:
            years = int(re.search(r'(\d+) years ago', time_str).group(1))
            return today - datetime.timedelta(days=years * 365)  # Best approximation I could think of ...
        else:
            return today

class Command(BaseCommand):
    help = 'Scrapes light novels from LightNovelPub and updates the database.'

    def handle(self, *args, **kwargs):
        """
        Handles the command execution for scraping light novels.

        Executes the scraping process, calculates the duration of the operation, and logs the result.
        """
        start_time = datetime.datetime.now()
        scraper = LightNovelPubScraper()
        try:
            scraper.scrape_light_novel_pub()

            duration = datetime.datetime.now() - start_time
            formatted_duration = self.format_duration(duration)

            logger.info(f"Successfully executed scrapeLightNovelPub in {formatted_duration} ")
            self.stdout.write(self.style.SUCCESS('Successfully executed scrapeLightNovelPub'))
        except Exception as e:
            logger.error(f"An error occurred during scraping: {e}")
            raise CommandError(f"Scraping failed due to an error: {e}")
    
    @staticmethod
    def format_duration(duration):
        """
        Formats a duration into a human-readable string.

        Args:
            duration (datetime.timedelta): The duration to format.

        Returns:
            str: A string representing the duration in hours, minutes, and seconds.
        """
        seconds = duration.total_seconds()
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        seconds = int(seconds % 60)
        return f"{hours}h {minutes}m {seconds}s"

if __name__ == "__main__":
    Command().handle()