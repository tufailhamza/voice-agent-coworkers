from google import genai
from google.genai import types
from dotenv import load_dotenv
import os
import time
from typing import Optional, Dict, Any


class WebSearch:
    _client = None
    _config = None

    @classmethod
    def _initialize(cls):
        if cls._client is None:
            load_dotenv()
            api_key = os.getenv("GOOGLE_API_KEY")
            cls._client = genai.Client(api_key=api_key)
            cls._config = types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )

    @classmethod
    def search(cls, query: str, model: str = "gemini-2.0-flash-lite") -> Dict[str, Any]:
        cls._initialize()
        start_time = time.time()

        try:
            response = cls._client.models.generate_content(
                model=model,
                contents=query,
                config=cls._config,
            )

            end_time = time.time()
            execution_time = end_time - start_time

            return {
                "query": query,
                "response": response.text,
                "raw_response": response,
                "execution_time": execution_time,
                "success": True,
            }

        except Exception as e:
            end_time = time.time()
            execution_time = end_time - start_time

            return {
                "query": query,
                "error": str(e),
                "execution_time": execution_time,
                "success": False,
            }


# # Example usage: python3 src/services/websearch.py
# if __name__ == "__main__":
#     for query in [
#         "Can you recommend some good places to eat in islamabad?",
#         "Whats the weather in Karachi rightnow?",
#         "Can you tell me about the crypto news right now?",
#     ]:
#         try:
#             result = WebSearch.search(query)
#             print(f"Response: {result['response']}")
#             print(f"Search successful: {result['success']}")
#             print(f"Execution time: {result['execution_time']:.2f} seconds")
#             print('-'*100)
#             # print(f"Raw response: {result['raw_response']}")
#         except Exception as e:
#             print(f"Error: {e}")
