import sys
from pathlib import Path

src_path = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(src_path))
import json
import os
from typing import List, Dict, Any, Optional
from src.agents.ryan.core.utils import openrouter_chat_completion
from src.utils.logger import Logger
from src.agents.ryan.core.prompts import PLANNER_AGENT_PROMPT, SAMPLE_WORKFLOWS

logging = Logger(__name__)


class RyanPlannerAgent:
    def __init__(self, model: str = "openai/gpt-4o-mini"):
        self.model = model
        self.planning_prompt = PLANNER_AGENT_PROMPT

    async def get_related_workflows(self, user_query: str) -> str:
        # TODO: get sample workflows from the user query from Vector DB for future usecases
        return SAMPLE_WORKFLOWS

    async def create_plan(self, user_query: str) -> Dict[str, Any]:
        try:
            related_workflows = await self.get_related_workflows(user_query)

            messages = [
                {"role": "system", "content": self.planning_prompt},
                {
                    "role": "user",
                    "content": f"""
                <user_query>{user_query}</user_query>
                <related_workflows>{related_workflows}</related_workflows>
                """,
                },
            ]

            logging.info(f"🎯 Generating plan for query: {user_query}...")

            response = openrouter_chat_completion(
                messages=messages,
                model=self.model,
                max_tokens=2048,
                temperature=0.3,
                timeout=60,
            )
            if "choices" in response and len(response["choices"]) > 0:
                content = response["choices"][0]["message"]["content"]
                return content
            else:
                raise Exception("No response from OpenRouter")

        except Exception as e:
            logging.error(f"Error generating plan: {e}")
            return None


if __name__ == "__main__":
    import asyncio

    async def test_planner():
        planner = RyanPlannerAgent()
        plan = await planner.create_plan("How do I find new candidates for my company?")
        print(plan)

    asyncio.run(test_planner())
