import os
import random
import requests
from bs4 import BeautifulSoup
from groq import Groq
from datetime import datetime
from twilio.rest import Client
import logging
import json
from twilio.base.exceptions import TwilioRestException
import re

# Set up logging
logging.basicConfig(filename='daily_scraper.log', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Set up Groq client
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# Set up Twilio client
twilio_client = Client(os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN"))

phone_numbers = ['+13042164370', '+17655862276', '+19259807244', '+13049063820']

def get_formatted_date():
    # Return the current date formatted for Wikipedia URL
    return datetime.now().strftime("%Y %B %d")

def interesting_info():
    # Generate interesting information using the LLM API
    prompt_template = """
    Tell me a random obscure, interesting, and enriching information. This can't be about jellyfishes. 
    These can be anything random from physics, biology, animals, plants, computer science, maths, psychology, economics, 
    history, politcal science, to pretty much anything.
    
    Return the output in a JSON format.
    
    This is the output:
    {{
        "fact": "<interesting info>"
    }}

    """

    prompt = prompt_template.format()
    
    try:
        logging.info(f"Sending request to Groq API for interesting info")
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model="llama3-8b-8192",
            temperature=1.5,
        )
        # Parse the returned message content
        response_content = chat_completion.choices[0].message.content

        # Extract JSON object
        json_str = re.search(r'{.*}', response_content, re.DOTALL)
        if json_str:
            interesting_fact = json.loads(json_str.group())
        else:
            raise ValueError("No valid JSON object found in the LLM response.")
        
        return interesting_fact

    except Exception as e:
        logging.error(f"Error in Groq API call for interesting info: {str(e)}")
        return {"error": f"Failed to retrieve interesting info : {str(e)}"}

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

    events = dict()

    print(f"Processing main content for date: {date}")
    # Extract the first-level <li> elements from the first-level <ul> only
    for ul_tag in main_content.find_all('ul', recursive=False):
        
        for li_tag in ul_tag.find_all('li', recursive=False):
            # Extract the main event title (from <a> tag or direct text)
            title_tag = li_tag.find('a')
            if title_tag:
                title = title_tag.get_text(strip=True)
            else:
                title = li_tag.get_text(strip=True)
            
            section_text = li_tag.get_text(strip=True)

            events[title] = section_text


    print(f"Finished processing main content. Total events found: {len(events)}")

    if not events:
        print(f"No events found for the specified date: {date}")
    else:
        print(f"Total events found: {len(events)}")

    return events

def summarize_with_groq(title, section_text):
    logging.info(f"Starting summarize_with_groq function")
    
    # Prompt template now expects a JSON output
    prompt_template = """You will summarize the key event of the day in a JSON format. The structure of the JSON should be as follows:

    {{
        "summary": {{
            "title": "{title}",
            "section_text": "- <summary point 1>\\n- <summary point 2>\\n- <summary point 3>"
        }}
    }}

    Provide a very simple, concise, three-point summarization. Make it extremely concise. If there is a name, political party, or geographical region 
    mentioned, then please briefly explain that.

    
    This is the title: {title}

    Here is the text to summarize:
    {section_text}
    """

    prompt = prompt_template.format(
        title=title,
        section_text=section_text
    )

    try:
        logging.info(f"Sending request to Groq API for event")
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model="llama3-8b-8192",
        )
        # Parse the returned message content
        response_content = chat_completion.choices[0].message.content

        # Extract JSON object using regular expression
        json_str = re.search(r'{.*}', response_content, re.DOTALL)
        if json_str:
            json_summary = json.loads(json_str.group())
        else:
            raise ValueError("No valid JSON object found in the LLM response.")
        
        return json_summary

    except json.JSONDecodeError as e:
        logging.error(f"Error parsing JSON response: {str(e)}")
        print(f"Error parsing JSON response for event: {title}")
        return {"error": "Invalid JSON response"}

    except Exception as e:
        logging.error(f"Error in Groq API call: {str(e)}")
        print(f"Error in Groq API call for event: {title}")
        return {"error": "Error summarizing event"}

def format_summary_for_whatsapp(summary):
    """
    Formats the JSON summary and interesting info into a string suitable for WhatsApp.
    """
    if "error" in summary:
        return f"Error: {summary['error']}"
    
    formatted_message = ""
    formatted_message += f"*Headline:* {summary['summary']['title']}\n"
    formatted_message += f"*Event:*\n {summary['summary']['section_text']}\n"

    return formatted_message

def send_whatsapp_message(to_number, message):
    account_sid = os.environ['TWILIO_ACCOUNT_SID']
    auth_token = os.environ['TWILIO_AUTH_TOKEN']
    client = Client(account_sid, auth_token)

    masked_number = f"xxx-xxx-{to_number[-4:]}"
    logging.info(f"Sending message to {masked_number}")

    try:
        sent_message = client.messages.create(
            from_='whatsapp:+14155238886',
            body=message,
            to=f"whatsapp:{to_number}"
        )
        logging.info(f"Message sent successfully to {masked_number}. SID: {sent_message.sid}")
        return True, sent_message.sid
    except TwilioRestException as e:
        logging.error(f"Failed to send message to {masked_number}. Error: {str(e)}")
        return False, None

def check_message_status(message_sid):
    account_sid = os.environ['TWILIO_ACCOUNT_SID']
    auth_token = os.environ['TWILIO_AUTH_TOKEN']
    client = Client(account_sid, auth_token)

    try:
        message = client.messages(message_sid).fetch()
        logging.info(f"Message {message_sid} status: {message.status}")
        if message.error_code:
            logging.warning(f"Message {message_sid} has error code: {message.error_code}")
            logging.warning(f"Error message: {message.error_message}")
        return message.status
    except TwilioRestException as e:
        logging.error(f"Error checking message status for SID {message_sid}: {str(e)}")
        return None

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

        # First message with Daily Summary
        first_message = f"*Daily Summary:* {today}"
        for number in phone_numbers:
            success, message_sid = send_whatsapp_message(number, first_message)
            if success:
                status = check_message_status(message_sid)
                logging.info(f"Message {message_sid} to {number} status: {status}")
            else:
                logging.error(f"Failed to send daily summary to {number}")

        # Get interesting fact from the LLM
        interesting_fact = interesting_info()
        interesting_fact = f"*Interesting Fact:* {interesting_fact.get("fact")}"

        # Process each event one at a time and send summary
        for event in events:
            summary = summarize_with_groq(event, events[event])

            if not summary or "error" in summary:
                logging.error(f"Failed to generate summary for event.")
                continue

            # Format the summary into a string suitable for WhatsApp
            formatted_message = format_summary_for_whatsapp(summary)
            
            for number in phone_numbers:
                success, message_sid = send_whatsapp_message(number, formatted_message)

                if success:
                    status = check_message_status(message_sid)
                    logging.info(f"Message {message_sid} to {number} status: {status}")
                else:
                    logging.error(f"Failed to send summary to {number}")

        for number in phone_numbers:
            success, message_sid = send_whatsapp_message(number, interesting_fact)
            if success:
                status = check_message_status(message_sid)
                logging.info(f"Message {message_sid} to {number} status: {status}")
            else:
                logging.error(f"Failed to send daily summary to {number}")

        logging.info("Main function execution completed successfully")
        return events

    except Exception as e:
        logging.error(f"Error in main function: {str(e)}", exc_info=True)
        return None
    finally:
        logging.info("Main function execution finished")

if __name__ == "__main__":
    logging.info("Starting the main script execution")
    main()
    logging.info("Main script execution completed")
