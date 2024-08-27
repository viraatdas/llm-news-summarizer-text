import os
import requests
from bs4 import BeautifulSoup
from groq import Groq
from datetime import datetime
from twilio.rest import Client
import logging
import time
from twilio.base.exceptions import TwilioRestException

# Set up logging
logging.basicConfig(filename='daily_scraper.log', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Set up Groq client
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# Set up Twilio client
twilio_client = Client(os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN"))



def get_formatted_date():
    # Return the current date formatted for Wikipedia URL
    return datetime.now().strftime("%Y %B %d")

def scrape_wikipedia():
    date = get_formatted_date()
    formatted_date = date.replace(' ', '_')
    url = f"https://en.wikipedia.org/wiki/Portal:Current_events/{formatted_date}"
    print(f"Processing URL: {url}")
    try:
        response = requests.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
    except requests.RequestException as e:
        print(f"Error fetching URL {url}: {e}")
        return []

    main_content = soup.find('div', {'class': 'current-events-content description'})
    if not main_content:
        print(f"No content found for the specified URL: {url}")
        return []

    events = []
    current_main_category = None
    current_subcategory = None

    def extract_event_info(element):
        text = element.get_text(strip=True)
        urls = [a['href'] if a['href'].startswith('http') else f"https://en.wikipedia.org{a['href']}" for a in element.find_all('a', href=True)]
        return text, urls

    print(f"Processing main content for date: {date}")
    for section in main_content.find_all(['h2', 'ul']):
        if section.name == 'h2':
            date_text = section.get_text(strip=True)
            if date in date_text:
                print(f"Found date header: {date_text}")
                continue
        elif section.name == 'ul':
            for item in section.find_all('li', recursive=False):
                if item.find('b'):  # Main category
                    current_main_category = item.find('b').get_text(strip=True)
                    events.append({"type": "main_category", "text": current_main_category})
                    current_subcategory = None
                    print(f"Found main category: {current_main_category}")
                else:  # Event or subcategory
                    event_text, event_urls = extract_event_info(item)
                    if item.find('a', {'class': 'mw-redirect'}):  # Subcategory
                        current_subcategory = event_text
                        events.append({
                            "type": "subcategory",
                            "main_category": current_main_category,
                            "text": current_subcategory,
                            "urls": event_urls
                        })
                        print(f"Found subcategory: {current_subcategory}")
                    else:  # Event
                        events.append({
                            "type": "event",
                            "main_category": current_main_category,
                            "subcategory": current_subcategory,
                            "text": event_text,
                            "urls": event_urls
                        })
                        print(f"Found event: {event_text}")

                # Check for nested events
                nested_list = item.find('ul')
                if nested_list:
                    for nested_item in nested_list.find_all('li', recursive=False):
                        nested_text, nested_urls = extract_event_info(nested_item)
                        events.append({
                            "type": "nested_event",
                            "main_category": current_main_category,
                            "subcategory": current_subcategory,
                            "text": nested_text,
                            "urls": nested_urls
                        })
                        print(f"Found nested event: {nested_text}")

    print(f"Finished processing main content. Total events found: {len(events)}")

    if not events:
        print(f"No events found for the specified date: {date}")
    else:
        print(f"Total events found: {len(events)}")

    return events


def summarize_with_groq(events, today):
    logging.info(f"Starting summarize_with_groq function with {len(events)} events for {today}")
    formatted_events = []
    current_main_category = None

    for event in events:
        if event['type'] == 'main_category':
            current_main_category = event['text']
            formatted_events.append(f"\n**{current_main_category}**")
        elif event['type'] in ['event', 'nested_event', 'subcategory']:
            event_text = f"- {event['text']}"
            formatted_events.append(event_text)

    events_text = "\n".join(formatted_events)
    logging.debug(f"Formatted events text (first 500 chars): {events_text[:500]}...")

    prompt_template = """Provide an extremely concise summary of the key events from the given text. Format the summary as follows:

    *Daily summary {today}*

    *{category}*
    • <very brief high level news for this category>
    • <another brief point if necessary>

    - Use bullet points (•) for individual events or facts.
    - Keep each bullet point to one line if possible.
    - Prioritize the most important information.
    - Use line breaks to separate different categories.
    - Keep the language concise and unbiased.
    - Format using Twilio's lightweight markup (use * for bold)

    Here's the text to summarize:

    {chunk}

    Please provide a well-formatted, extremely concise summary following the instructions above:"""

    chunks = split_message(events_text, chunk_size=4000)
    logging.info(f"Split events text into {len(chunks)} chunks")
    summaries = []

    for i, chunk in enumerate(chunks):
        logging.info(f"Processing chunk {i+1}/{len(chunks)}")
        prompt = prompt_template.format(
            today=today,
            category=events[0]['text'] if i == 0 else "Continued",
            chunk=chunk
        )

        try:
            logging.info(f"Sending request to Groq API for chunk {i+1}")
            chat_completion = client.chat.completions.create(
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                model="llama3-8b-8192",
            )
            summary = chat_completion.choices[0].message.content
            logging.info(f"Received summary for chunk {i+1} (first 200 chars): {summary[:200]}...")
            summaries.append(summary)
        except Exception as e:
            logging.error(f"Error in Groq API call for chunk {i+1}: {str(e)}")
            summaries.append(f"Error summarizing chunk {i+1}")

    final_summary = "\n\n".join(summaries)
    logging.info(f"Final summary generated (length: {len(final_summary)} chars)")
    return final_summary

def split_message(message, chunk_size=1000):
    lines = message.split('\n')
    chunks = []
    current_chunk = []
    current_length = 0

    for line in lines:
        if current_length + len(line) + 1 > chunk_size:
            chunks.append('\n'.join(current_chunk))
            current_chunk = [line]
            current_length = len(line)
        else:
            current_chunk.append(line)
            current_length += len(line) + 1

    if current_chunk:
        chunks.append('\n'.join(current_chunk))

    return chunks

def main():
    logging.info("Starting main function execution")
    try:
        # Get formatted date for Wikipedia URL
        formatted_date = get_formatted_date()
        today = datetime.strptime(formatted_date, "%Y %B %d").strftime("%Y-%m-%d")
        logging.info(f"Processing events for date: {today}")

        logging.info(f"Scraping Wikipedia for date: {formatted_date}")
        events = scrape_wikipedia()
        if not events:
            logging.error(f"No events found on Wikipedia for {formatted_date}")
            raise ValueError(f"No events found on Wikipedia for {formatted_date}")

        logging.info(f"Found {len(events)} events")
        logging.debug("Raw event details:")
        for i, event in enumerate(events, 1):
            logging.debug(f"Event {i}: Type: {event['type']}, "
                          f"Main Category: {event.get('main_category', 'N/A')}, "
                          f"Subcategory: {event.get('subcategory', 'N/A')}, "
                          f"Text: {event['text']}, "
                          f"URLs: {', '.join(event.get('urls', []))}")

        logging.debug("Events hierarchy:")
        current_main_category = None
        current_subcategory = None
        for event in events:
            if event['type'] == 'main_category':
                current_main_category = event['text']
                logging.debug(f"* {current_main_category}")
            elif event['type'] == 'subcategory':
                current_subcategory = event['text']
                logging.debug(f"  - {current_subcategory}")
            elif event['type'] in ['event', 'nested_event']:
                logging.debug(f"    • {event['text']}")

        logging.info("Generating summary...")
        summary = summarize_with_groq(events, today)
        if not summary:
            logging.error("Failed to generate summary")
            raise ValueError("Failed to generate summary")
        logging.info("Summary generated successfully")

        logging.info(f"Daily Summary for {today}:")
        summary_chunks = split_message(summary)
        for i, chunk in enumerate(summary_chunks, 1):
            logging.debug(f"Chunk {i}:\n{chunk}")

        logging.info("Debug information:")
        logging.info(f"Total events: {len(events)}")
        logging.info(f"Summary length: {len(summary)} characters")
        logging.info(f"Number of summary chunks: {len(summary_chunks)}")

        logging.debug("Raw events data (first 5 events):")
        import json
        logging.debug(json.dumps(events[:5], indent=2))

        logging.info("Main function execution completed successfully")
        return summary

    except Exception as e:
        error_message = f"Error in main function: {str(e)}"
        logging.error(error_message, exc_info=True)
        return None
    finally:
        logging.info("Main function execution finished")

def send_whatsapp_message(to_number, messages):
    account_sid = os.environ['TWILIO_ACCOUNT_SID']
    auth_token = os.environ['TWILIO_AUTH_TOKEN']
    client = Client(account_sid, auth_token)

    message_sids = []
    masked_number = f"xxx-xxx-{to_number[-4:]}"
    logging.info(f"Attempting to send {len(messages)} messages to {masked_number}")

    for i, message in enumerate(messages, 1):
        max_retries = 3
        retry_count = 0
        while retry_count < max_retries:
            try:
                logging.debug(f"Sending message {i}/{len(messages)} to {masked_number}")
                logging.debug(f"Request payload: from_='whatsapp:+14155238886', to='whatsapp:{to_number}', body_length={len(message)}")
                logging.debug(f"Message content: {message}")
                sent_message = client.messages.create(
                    from_='whatsapp:+14155238886',
                    body=message,
                    to=f"whatsapp:{to_number}"
                )
                message_sids.append(sent_message.sid)
                logging.info(f"Message {i}/{len(messages)} sent successfully to {masked_number}. SID: {sent_message.sid}")
                logging.debug(f"Full Twilio API response: {sent_message.__dict__}")
                break
            except TwilioRestException as e:
                retry_count += 1
                logging.warning(f"Attempt {retry_count} failed to send message {i}/{len(messages)} to {masked_number}. Error: {str(e)}")
                logging.error(f"Twilio error code: {e.code}")
                logging.error(f"Twilio error message: {e.msg}")
                logging.error(f"Twilio error details: {e.details}")
                if retry_count == max_retries:
                    logging.error(f"Failed to send message {i}/{len(messages)} to {masked_number} after {max_retries} attempts.")
                    return False, message_sids
                time.sleep(5)  # Wait for 5 seconds before retrying

    logging.info(f"Successfully sent {len(message_sids)}/{len(messages)} messages to {masked_number}")
    return True, message_sids

def check_message_status(message_sid):
    account_sid = os.environ['TWILIO_ACCOUNT_SID']
    auth_token = os.environ['TWILIO_AUTH_TOKEN']
    client = Client(account_sid, auth_token)

    try:
        message = client.messages(message_sid).fetch()
        logging.info(f"Message {message_sid} status: {message.status}")
        logging.debug(f"Full message details: {message.__dict__}")
        if message.error_code:
            logging.warning(f"Message {message_sid} has error code: {message.error_code}")
            logging.warning(f"Error message: {message.error_message}")
        return message.status
    except TwilioRestException as e:
        logging.error(f"Error checking message status for SID {message_sid}: {str(e)}")
        logging.error(f"Error code: {e.code}")
        logging.error(f"Error details: {e.details}")
        return None

if __name__ == "__main__":
    logging.info("Starting the main script execution")
    try:
        logging.info("Calling main() function to generate summary")
        summary = main()
        if summary:
            logging.info("Summary generated successfully. Preparing to send WhatsApp messages")
            phone_numbers = ['+13042164370', '+17655862276', '+19259807244']

            # Split the summary into separate messages for each category
            messages = summary.split('\n\n')

            for number in phone_numbers:
                logging.info(f"Sending messages to {number}")
                delivery_status = []

                for i, message in enumerate(messages, 1):
                    logging.info(f"Sending message {i}/{len(messages)} to {number}")
                    success, message_sids = send_whatsapp_message(number, message)

                    if success:
                        for sid in message_sids:
                            status = check_message_status(sid)
                            delivery_status.append(status)
                            logging.info(f"Message {sid} to {number} status: {status}")
                            if status != 'delivered':
                                logging.warning(f"Message {sid} to {number} status: {status}")
                    else:
                        delivery_status.extend([False] * len(message_sids))
                        logging.error(f"Failed to send message {i} to {number}")

                successful_deliveries = sum(1 for status in delivery_status if status == 'delivered')
                logging.info(f"Sent {successful_deliveries}/{len(messages)} messages to {number}")

                if successful_deliveries < len(messages):
                    failed_deliveries = len(messages) - successful_deliveries
                    logging.error(f"Failed to deliver {failed_deliveries} messages to {number}")
        else:
            logging.warning("No summary generated. Unable to send WhatsApp messages.")
    except Exception as e:
        logging.error(f"An error occurred in the main execution: {str(e)}")
    finally:
        logging.info("Main script execution completed")
