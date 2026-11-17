import re
import time
from groq import Groq
import os
import json
import requests
import re
from typing import Dict, Any, Generator, Iterable, Optional, Union
from dotenv import load_dotenv
import time

load_dotenv()
from src.utils.logger import Logger

logging = Logger(__name__)

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


def translate(text: str, target_language: str = "English") -> str:
    # Build prompt for LLM
    llm_prompt = f"""<instructions>You are a professional translator. Translate the following text accurately and naturally to {target_language}.
Only output the translated text AND NOTHING ELSE:<instructions>
<text>
{text}
</text>
"""

    try:
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
        logging.info(
            f"😈 translation execution time: {end_time - start_time:.2f} seconds"
        )

        # Extract reply text
        reply_text = response.choices[0].message.content.strip()
        logging.info("😈 LLM translation reply:", reply_text)

        return reply_text

    except Exception as e:
        logging.info(f"😈 translation error: {e}")
        return text


def is_english(text: str) -> bool:
    return bool(NON_ENGLISH_PATTERN.match(text))
