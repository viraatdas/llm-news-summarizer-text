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
    print(f"Starting summarize_with_groq function with {len(events)} events for {today}")
    # Prepare the events data for summarization
    formatted_events = []
    current_main_category = None
    current_subcategory = None

    for event in events:
        if event['type'] == 'main_category':
            current_main_category = event['text']
            formatted_events.append(f"\n**{current_main_category}**")
        elif event['type'] == 'subcategory':
            current_subcategory = event['text']
            formatted_events.append(f"\n*{current_subcategory}*")
        elif event['type'] in ['event', 'nested_event']:
            event_text = f"- {event['text']}"
            if event['urls']:
                event_text += f" [Links: {', '.join(event['urls'])}]"
            formatted_events.append(event_text)

    events_text = "\n".join(formatted_events)
    print(f"Formatted events text (first 500 chars): {events_text[:500]}...")

    prompt_template = """Provide an extremely concise summary of the key events and their historical context from the given text. Format the summary as follows:

    **Daily summary {today}**

    **{first_category}**
    <very brief high level news for this category, use bullet points>

    **Relevant context**
    <concise relevant context like where the cities are and bite-sized information to help understand the events>

    ...

    - Use bullet points for individual events or facts.
    - Keep each bullet point to one line if possible.
    - Prioritize the most important information.
    - Use line breaks to separate different sections.
    - Keep the language concise and unbiased.
    - The relevant context section should have information for things that had a link
    - Format using Twilio's lightweight markup

    Here's the text to summarize:

    {chunk}

    Please provide a well-formatted, extremely concise summary following the instructions above:"""

    chunks = split_message(events_text, chunk_size=4000)  # Adjust chunk_size as needed
    print(f"Split events text into {len(chunks)} chunks")
    summaries = []

    for i, chunk in enumerate(chunks):
        print(f"Processing chunk {i+1}/{len(chunks)}")
        prompt = prompt_template.format(
            today=today,
            first_category=events[0]['text'] if i == 0 else "Continued",
            chunk=chunk
        )

        try:
            print(f"Sending request to Groq API for chunk {i+1}")
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
            print(f"Received summary for chunk {i+1} (first 200 chars): {summary[:200]}...")
            summaries.append(summary)
        except Exception as e:
            print(f"Error in Groq API call for chunk {i+1}: {str(e)}")
            summaries.append(f"Error summarizing chunk {i+1}")

    final_summary = "\n\n".join(summaries)
    print(f"Final summary generated (length: {len(final_summary)} chars)")
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
    try:
        # Get formatted date for Wikipedia URL
        formatted_date = get_formatted_date()
        today = datetime.strptime(formatted_date, "%Y %B %d").strftime("%Y-%m-%d")

        print(f"[DEBUG] Scraping Wikipedia for date: {formatted_date}")
        events = scrape_wikipedia()
        if not events:
            raise ValueError(f"No events found on Wikipedia for {formatted_date}")

        print(f"\n[DEBUG] Found {len(events)} events. Raw event details:")
        for i, event in enumerate(events, 1):
            print(f"Event {i}:")
            print(f"  Type: {event['type']}")
            print(f"  Main Category: {event.get('main_category', 'N/A')}")
            print(f"  Subcategory: {event.get('subcategory', 'N/A')}")
            print(f"  Text: {event['text']}")
            print(f"  URLs: {', '.join(event.get('urls', []))}")
            print("---")

        print("\n[DEBUG] Events hierarchy:")
        current_main_category = None
        current_subcategory = None
        for event in events:
            if event['type'] == 'main_category':
                current_main_category = event['text']
                print(f"\n* {current_main_category}")
            elif event['type'] == 'subcategory':
                current_subcategory = event['text']
                print(f"  - {current_subcategory}")
            elif event['type'] in ['event', 'nested_event']:
                print(f"    • {event['text']}")

        print("\n[DEBUG] Generating summary...")
        summary = summarize_with_groq(events, today)
        print(f"[DEBUG] Summary generated: {'Yes' if summary else 'No'}")
        if not summary:
            raise ValueError("Failed to generate summary")

        print(f"\n[DEBUG] Daily Summary for {today}:")
        summary_chunks = split_message(summary)
        for i, chunk in enumerate(summary_chunks, 1):
            print(f"[DEBUG] Chunk {i}:")
            print(chunk)
            print("---")

        print("\n[DEBUG] Debug information:")
        print(f"Total events: {len(events)}")
        print(f"Summary length: {len(summary)} characters")
        print(f"Number of summary chunks: {len(summary_chunks)}")

        print("\n[DEBUG] Raw events data (first 5 events):")
        import json
        print(json.dumps(events[:5], indent=2))

        return summary

    except Exception as e:
        error_message = f"[ERROR] Error in main function: {str(e)}"
        logging.error(error_message)
        return None
    finally:
        logging.info("\n[DEBUG] Script execution completed.")

def send_whatsapp_message(to_number, message):
    account_sid = os.environ['TWILIO_ACCOUNT_SID']
    auth_token = os.environ['TWILIO_AUTH_TOKEN']
    client = Client(account_sid, auth_token)

    chunks = split_message(message, chunk_size=1600)
    message_sids = []

    for i, chunk in enumerate(chunks, 1):
        max_retries = 3
        retry_count = 0
        while retry_count < max_retries:
            try:
                message = client.messages.create(
                    from_='whatsapp:+14437752876',
                    body=chunk,
                    to=f"whatsapp:{to_number}"
                )
                message_sids.append(message.sid)
                logging.info(f"Chunk {i}/{len(chunks)} sent successfully to {to_number}. SID: {message.sid}")
                break
            except TwilioRestException as e:
                retry_count += 1
                logging.warning(f"Attempt {retry_count} failed to send chunk {i}/{len(chunks)} to {to_number}. Error: {str(e)}")
                if retry_count == max_retries:
                    logging.error(f"Failed to send chunk {i}/{len(chunks)} to {to_number} after {max_retries} attempts.")
                    return False, message_sids
                time.sleep(5)  # Wait for 5 seconds before retrying

    return True, message_sids

def check_message_status(message_sid):
    account_sid = os.environ['TWILIO_ACCOUNT_SID']
    auth_token = os.environ['TWILIO_AUTH_TOKEN']
    client = Client(account_sid, auth_token)

    try:
        message = client.messages(message_sid).fetch()
        logging.info(f"Message {message_sid} status: {message.status}")
        return message.status
    except TwilioRestException as e:
        logging.error(f"Error checking message status for SID {message_sid}: {str(e)}")
        return None

if __name__ == "__main__":
    try:
        summary = main()
        if summary:
            phone_numbers = ['+13042164370', '+17655862276', '+19259807244']
            message_chunks = split_message(summary, chunk_size=1600)
            for number in phone_numbers:
                delivery_status = []
                for chunk in message_chunks:
                    success, message_sids = send_whatsapp_message(number, chunk)
                    if success:
                        for sid in message_sids:
                            status = check_message_status(sid)
                            delivery_status.append(status)
                            if status != 'delivered':
                                logging.warning(f"Message {sid} to {number} status: {status}")
                    else:
                        delivery_status.extend([False] * len(message_sids))
                        logging.error(f"Failed to send message chunk to {number}")

                successful_deliveries = sum(1 for status in delivery_status if status == 'delivered')
                logging.info(f"Sent {successful_deliveries}/{len(message_chunks)} message chunks to {number}")

                if successful_deliveries < len(message_chunks):
                    failed_deliveries = len(message_chunks) - successful_deliveries
                    logging.error(f"Failed to deliver {failed_deliveries} chunks to {number}")
        else:
            logging.warning("No summary generated. Unable to send WhatsApp messages.")
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")
