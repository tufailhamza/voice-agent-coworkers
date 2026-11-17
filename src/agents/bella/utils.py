import asyncio
import collections.abc
from dataclasses import asdict
from datetime import datetime, date
import json
import pytz


def print_attribute_names(obj):
    if isinstance(obj, dict):
        print("Dictionary keys:")
        for key in obj.keys():
            print(f" - {key}")
    else:
        print("Object attributes:")
        for attr in dir(obj):
            if not attr.startswith("_") and hasattr(obj, attr):
                print(f" - {attr}")


def sanitize_for_rpc(obj):
    """Sanitize object for RPC by converting datetimes to ISO strings with timezone preservation."""

    def _sanitize(value):
        if isinstance(value, dict):
            return {k: _sanitize(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [_sanitize(v) for v in value]
        elif isinstance(value, (datetime, date)):
            # Preserve timezone information if present
            if isinstance(value, datetime) and value.tzinfo is not None:
                return value.isoformat()
            elif isinstance(value, datetime):
                # If naive datetime, assume it's local timezone (Asia/Karachi)
                local_tz = pytz.timezone("Asia/Karachi")
                return local_tz.localize(value).isoformat()
            else:
                # For date objects, convert to datetime with local timezone
                local_tz = pytz.timezone("Asia/Karachi")
                return local_tz.localize(
                    datetime.combine(value, datetime.min.time())
                ).isoformat()
        elif value is None:
            return None
        else:
            return value

    return _sanitize(obj)


async def update_tool_info(userdata, tool_name, status):
    # Update tool_called and tool_status
    userdata.tool_called = tool_name
    userdata.tool_status = status

    # Determine modal based on tool name
    todo_events = {"add_todo", "get_todo", "update_todo", "delete_todo"}
    calendar_events = {
        "schedule_meeting_with_participants",
        "list_events",
        "update_event",
        "delete_event",
    }
    meeting_query_events = {"rag_search_meetings"}

    if tool_name in todo_events:
        userdata.modal = "Todo"
    elif tool_name in calendar_events:
        userdata.modal = "Calendar"
    elif tool_name in meeting_query_events:
        userdata.modal = "Meeting Query"
    else:
        userdata.modal = ""


async def _materialize_llm_result(result, userdata):
    if asyncio.iscoroutine(result):
        result = await result

    if isinstance(result, collections.abc.AsyncIterable):
        # Stream into a list (to inspect and re-yield)
        items = [chunk async for chunk in result]
        for chunk in items:
            # with open("chunks.txt", "a") as f:
            #     f.write(str(chunk) + "\n")
            if getattr(chunk, "delta", None) and getattr(
                chunk.delta, "tool_calls", None
            ):
                for tool_call in chunk.delta.tool_calls:
                    if getattr(tool_call, "name", None):
                        # userdata.tool_called = tool_call.name
                        pass

        async def yield_chunks():
            for chunk in items:
                yield chunk

        return yield_chunks()

    else:
        # Single item (str or ChatChunk)
        # print("LLM Output:", result)
        return result


import os
import json
import requests
import re
from typing import Dict, Any, Generator, Iterable, Optional, Union
from dotenv import load_dotenv
import time

load_dotenv()

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
NON_ENGLISH_PATTERN = re.compile(
    r'^[\w\s\d!"#$%&\'()*+,\-./:;<=>?@[\\\]^_`{|}~'
    r"\u00A0\u2000-\u206F\u20A0-\u20CF\u2100-\u214F\u2190-\u21FF\u2200-\u22FF"
    r"\U0001F000-\U0001FAFF]*$"
)


def openrouter_chat_completion(
    messages: Optional[Iterable[Dict[str, str]]] = None,
    *,
    model: str = "anthropic/claude-3.5-sonnet",
    max_tokens: int = 4096,
    temperature: float = 1,
    top_p: float = 1,
    timeout: int = 300,
    extra_headers: Optional[Dict[str, str]] = None,
):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY')}",
    }
    if extra_headers:
        headers.update(extra_headers)

    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "messages": list(messages),
    }

    response = requests.post(
        OPENROUTER_API_URL,
        headers=headers,
        json=payload,
        timeout=timeout,
    )

    response.raise_for_status()
    return response.json()


import re
import time
from groq import Groq


def translate(text: str, target_language: str = "English") -> str:
    # Build prompt for LLM
    llm_prompt = f"""
You are a professional translator. Translate the following text accurately and naturally to {target_language}.
Only output the translated text, nothing else:

{text}
"""

    try:
        # Create Groq or LLM client (replace Groq() with your actual client)
        client = Groq()

        start_time = time.time()
        response = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": llm_prompt}],
            temperature=0.3,
            top_p=0.9,
            max_tokens=1000,
        )
        end_time = time.time()
        print(f"😈 translation execution time: {end_time - start_time:.2f} seconds")

        # Extract reply text
        reply_text = response.choices[0].message.content.strip()
        print("LLM translation reply:", reply_text)

        return reply_text

    except Exception as e:
        print(f"translation error: {e}")
        return text


def is_english(text: str) -> bool:
    return bool(NON_ENGLISH_PATTERN.match(text))


from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from typing import Tuple


def get_time_range(
    days_before: int = 10, days_after: int = 30, timezone: str = "Asia/Karachi"
) -> Tuple[str, str]:
    """
    Return (time_min, time_max) ISO8601 strings in the given timezone.
    Automatically handles leap years, month boundaries, etc.
    Example output: ("2025-10-10T00:00:00+05:00", "2025-11-09T23:59:59+05:00")
    """
    tz = ZoneInfo(timezone)
    today = datetime.now(tz).date()

    # Start at 00:00:00 on (today - days_before)
    start_date = today - timedelta(days=days_before)
    time_min_dt = datetime.combine(start_date, time.min).replace(tzinfo=tz)

    # End at 23:59:59 on (today + days_after)
    end_date = today + timedelta(days=days_after)
    time_max_dt = datetime.combine(end_date, time(23, 59, 59)).replace(tzinfo=tz)

    return time_min_dt.isoformat(), time_max_dt.isoformat()


# from indic_transliteration import sanscript
# from indic_transliteration.sanscript import transliterate as hindi_transliterate
# from transliterate import translit  # For Russian, Ukrainian, etc.

# def transliterate_text(text, language_code):
#     if language_code == 'en':  # If English, return as is
#         return text

#     elif language_code == 'hi':  # Hindi
#         return hindi_transliterate(text, sanscript.DEVANAGARI, sanscript.ITRANS)

#     elif language_code in ['ru', 'uk']:  # Russian or Ukrainian
#         return translit(text, 'ru', reversed=True)  # Cyrillic to Romanized

#     elif language_code == 'ja':  # Japanese
#         return translit(text, 'ja', reversed=True)

#     elif language_code == 'zh':  # Chinese
#         return translit(text, 'zh', reversed=True)

#     elif language_code == 'ko':  # Korean
#         return translit(text, 'ko', reversed=True)

#     else:
#         return text

# from openai import OpenAI
# from indic_transliteration import sanscript
# from indic_transliteration.sanscript import transliterate as hindi_transliterate
# from transliterate import translit  # For Cyrillic, Japanese, Chinese, Korean, etc.

# client = OpenAI()


# def correct_transliteration(original_text, language_code, transliterated_text):
#     """
#     Uses GPT-4.1-nano to correct and refine transliteration.
#     """
#     prompt = f"""
# You are a transliteration correction assistant.

# Original text (language code: {language_code}):
# {original_text}

# Current transliteration:
# {transliterated_text}

# Instructions:
# - Correct any transliteration errors.
# - Preserve names, tone, and meaning.
# - Do NOT translate — only fix spelling and phonetic issues.
# - Return ONLY the corrected transliteration text using Latin Alphabets
#     """

#     response = client.chat.completions.create(
#         model="gpt-4.1-nano",
#         messages=[{"role": "user", "content": prompt}],
#         temperature=0,
#     )

#     corrected = response.choices[0].message.content.strip().lower()
#     return corrected


# def transliterate_text(text, language_code):
#     """
#     Performs base transliteration and then corrects it with GPT-4.1-nano.
#     """
#     if language_code == "en":  # English: no transliteration needed
#         return text.lower()

#     elif language_code == "hi":  # Hindi → Romanized
#         transliterated = hindi_transliterate(
#             text, sanscript.DEVANAGARI, sanscript.ITRANS
#         )

#     elif language_code in ["ru", "uk"]:  # Russian / Ukrainian → Romanized
#         transliterated = translit(text, "ru", reversed=True)

#     elif language_code == "ja":  # Japanese → Romanized
#         transliterated = translit(text, "ja", reversed=True)

#     elif language_code == "zh":  # Chinese → Romanized (pinyin style)
#         transliterated = translit(text, "zh", reversed=True)

#     elif language_code == "ko":  # Korean → Romanized
#         transliterated = translit(text, "ko", reversed=True)

#     else:
#         transliterated = text

#     # ✅ Correct transliteration using lightweight GPT
#     corrected_text = correct_transliteration(text, language_code, transliterated)

#     print(f"Original {text}")
#     print(f"Transilterated {transliterated}")
#     print(f"Corrected {corrected_text}")

#     return corrected_text
