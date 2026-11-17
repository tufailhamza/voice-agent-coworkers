import logging
import json
from typing import List, Dict, Any
import asyncio

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from .config import Config
from .models import Transcript, MeetingSummary
from src.utils.exceptions import SummaryError

logger = logging.getLogger(__name__)


class SummaryService:
    """Service for generating meeting summaries"""

    def __init__(self, config: Config):
        self.config = config
        self.llm = ChatOpenAI(
            model=config.ai.summary_model,
            temperature=config.ai.summary_temperature,
            api_key=config.ai.openai_api_key,
        )

    async def generate_summary(
        self, transcripts: List[Transcript], user_id_to_name_map: Dict[int, str] = None
    ) -> MeetingSummary:
        """Generate meeting summary using chunked approach for each participant"""
        if not transcripts:
            raise SummaryError("No transcripts provided for summary generation")

        if user_id_to_name_map is None:
            user_id_to_name_map = {}

        logger.info(
            f"📊 User mapping status: {len(user_id_to_name_map)} users loaded for name conversion"
        )

        try:
            # Get unique participants (using IDs - for payloads)
            participants = list(set(t.speaker_name for t in transcripts))
            logger.info(
                f"📊 Processing summaries for {len(participants)} participants: {participants}"
            )

            # Helper function to get name from participant identity
            def get_participant_name(participant_identity: str) -> str:
                """Extract numeric ID and map to name, fallback to identity"""
                try:
                    if "-" in participant_identity:
                        numeric_id_str = participant_identity.split("-")[0]
                    else:
                        numeric_id_str = participant_identity
                    numeric_id = int(numeric_id_str)
                    name = user_id_to_name_map.get(numeric_id)
                    if name:
                        logger.debug(
                            f"✅ Mapped participant {participant_identity} (ID: {numeric_id}) → {name}"
                        )
                        return name
                    else:
                        logger.debug(
                            f"⚠️ No name mapping found for participant {participant_identity} (ID: {numeric_id}), using ID"
                        )
                except (ValueError, KeyError) as e:
                    logger.debug(
                        f"⚠️ Error extracting ID from {participant_identity}: {e}, using original"
                    )
                return participant_identity

            # Generate summary for each participant concurrently
            participant_summary_tasks = []
            for participant in participants:
                # Filter transcripts for this participant
                participant_transcripts = [
                    {
                        "speaker_name": t.speaker_name,
                        "text_content": t.text_content,
                        "timestamp": t.timestamp,
                        "participant_sid": t.participant_sid,
                    }
                    for t in transcripts
                    if t.speaker_name == participant
                ]

                # Use name for LLM prompt, but keep original participant ID for tracking
                participant_name = get_participant_name(participant)
                task = self.generate_participant_summary(
                    participant_name, participant_transcripts, original_id=participant
                )
                participant_summary_tasks.append(task)

            # Wait for all participant summaries to complete
            participant_summaries = await asyncio.gather(
                *participant_summary_tasks, return_exceptions=True
            )

            # Handle any exceptions and filter out failed summaries
            valid_summaries = []
            participant_names_for_summary = []  # Use names for final summary context
            for i, summary in enumerate(participant_summaries):
                participant_name = get_participant_name(participants[i])
                participant_names_for_summary.append(participant_name)

                if isinstance(summary, Exception):
                    logger.error(
                        f"❌ Failed to generate summary for {participants[i]}: {summary}"
                    )
                    valid_summaries.append(
                        f"**{participant_name}**: Error generating summary."
                    )
                else:
                    valid_summaries.append(summary)

            logger.info(f"📝 Generated {len(valid_summaries)} participant summaries")

            # Generate final combined summary (use names for better context)
            final_summary = await self.generate_final_summary(
                valid_summaries, participant_names_for_summary
            )

            # Create summary object
            summary = MeetingSummary(
                room_id=transcripts[0].room_id,
                session_id=transcripts[0].session_id,
                summary_content=final_summary,
                participants=participants,
            )

            logger.info("📋 Generated meeting summary successfully")
            return summary

        except Exception as e:
            logger.error(f"❌ Error generating summary: {e}")
            raise SummaryError(f"Failed to generate summary: {e}")

    def _format_transcripts(self, transcripts: List[Transcript]) -> str:
        """Format transcripts for LLM input"""
        return "\n".join([f"{t.speaker_name}: {t.text_content}" for t in transcripts])

    async def generate_participant_summary(
        self,
        participant_name: str,
        participant_transcripts: List[Dict[str, Any]],
        original_id: str = None,
    ) -> str:
        """Generate summary for a specific participant"""
        try:
            # Combine all transcripts for this participant
            participant_text = "\n".join(
                [t["text_content"] for t in participant_transcripts]
            )

            # Skip if participant has no meaningful content
            if len(participant_text.strip()) < 10:
                return f"**{participant_name}**: Limited participation in the meeting."

            summary_llm = ChatOpenAI(
                model=self.config.ai.summary_model,
                temperature=self.config.ai.summary_temperature,
                api_key=self.config.ai.openai_api_key,
            )

            message = HumanMessage(content=f"""
                Note: Translate the words if not in english and please give summary in english
                You are a meeting assistant. Based on the transcript below from participant "{participant_name}", 
                generate a concise summary of their contributions to the meeting.
                
                Focus on:
                1. Key points they discussed
                2. Questions they asked
                3. Decisions or opinions they shared
                4. Action items they committed to
                
                Keep All the details and concise (2-5 lines) and Keywords mentioned and translate any non-English content to English.
                
                Participant's transcript:
                \"\"\"
                {participant_text}
                \"\"\"
            """)

            response = await summary_llm.ainvoke([message])
            return f"**{participant_name}**: {response.content}"

        except Exception as e:
            logger.error(f"❌ Error generating summary for {participant_name}: {e}")
            return f"**{participant_name}**: Error generating summary for this participant."

    async def generate_final_summary(
        self, participant_summaries: List[str], participant_names: List[str]
    ) -> str:
        """Generate final combined summary from all participant summaries"""
        try:
            combined_summaries = "\n\n".join(participant_summaries)

            summary_llm = ChatOpenAI(
                model=self.config.ai.summary_model,
                temperature=self.config.ai.summary_temperature,
                api_key=self.config.ai.openai_api_key,
            )

            message = HumanMessage(content=f"""
                You are a meeting assistant. Based on the transcript below, generate a clear and concise meeting summary.
                #Mention all the details and Keywords
                #Include the following sections:

                #1. **Meeting Participants** – List all participants who spoke during the meeting.
                #2. **Key Topics Discussed** – Bullet points summarizing the main themes or agenda items.
                #3. **Participant Contributions** – Mention key inputs from different participants by name.
                #4. **Meeting Outcome / Conclusion** – Summarize the result of the meeting, including any decisions made or next steps agreed upon.
                
                Participants in this meeting: {', '.join(participant_names)}
                
                Individual participant summaries:
                \"\"\"
                {combined_summaries}
                \"\"\"
                
                Make the summary comprehensive but concise, focusing on actionable outcomes and key decisions.
            """)

            response = await summary_llm.ainvoke([message])
            return response.content

        except Exception as e:
            logger.error(f"❌ Error generating final summary: {e}")
            return "Error generating final meeting summary."

    async def extract_action_items(self, summary_text: str) -> List[Dict[str, str]]:
        """Extract action items from summary text using LLM"""
        try:
            action_items_llm = ChatOpenAI(
                model=self.config.ai.summary_model,
                temperature=self.config.ai.summary_temperature,
                api_key=self.config.ai.openai_api_key,
            )

            message = HumanMessage(content=f"""
                You are a meeting assistant. Based on the meeting summary below, extract all action items, tasks, or follow-up items mentioned.
                
                For each action item, identify:
                1. Title - A short, clear title for the action item (MUST be max 40 characters)
                2. Note - A detailed note/description of what needs to be done (MUST be max 80 characters)
                3. Priority - "high", "medium", or "low" based on urgency and importance
                
                IMPORTANT: Title must be maximum 40 characters and Note must be maximum 80 characters. Keep them concise.
                
                Return ONLY a valid JSON array of action items in this exact format:
                [
                    {{
                        "title": "Action item title",
                        "note": "Detailed note about the action",
                        "priority": "high"
                    }},
                    {{
                        "title": "Another action item",
                        "note": "Another note",
                        "priority": "medium"
                    }}
                ]
                
                If no action items are found, return an empty array: []
                
                Meeting Summary:
                \"\"\"
                {summary_text}
                \"\"\"
                
                Return ONLY the JSON array, no other text or explanation.
            """)

            response = await action_items_llm.ainvoke([message])

            # Parse JSON response
            action_items_text = response.content.strip()

            # Remove markdown code blocks if present
            if action_items_text.startswith("```"):
                lines = action_items_text.split("\n")
                action_items_text = (
                    "\n".join(lines[1:-1]) if len(lines) > 2 else action_items_text
                )

            action_items = json.loads(action_items_text)

            # Validate structure
            if not isinstance(action_items, list):
                logger.warning(
                    "⚠️ Action items response is not a list, returning empty array"
                )
                return []

            # Validate each item has required fields and apply post-filtering
            valid_items = []
            for item in action_items:
                # Check for both "description" and "note" (for backward compatibility)
                note = item.get("note") or item.get("description", "")
                if (
                    isinstance(item, dict)
                    and "title" in item
                    and note
                    and "priority" in item
                ):
                    # Ensure priority is valid
                    priority = item["priority"]
                    if priority not in ["high", "medium", "low"]:
                        priority = "medium"  # Default to medium if invalid

                    # Post-filter: Trim title to max 40 chars and note to max 80 chars
                    title = str(item["title"])
                    note_str = str(note)

                    # Trim if exceeds limits (critical requirement)
                    if len(title) > 40:
                        title = title[:40]
                        logger.warning(
                            f"⚠️ Action item title exceeded 40 chars, trimmed: {title}"
                        )

                    if len(note_str) > 80:
                        note_str = note_str[:80]
                        logger.warning(
                            f"⚠️ Action item note exceeded 80 chars, trimmed: {note_str}"
                        )

                    valid_items.append(
                        {
                            "title": title,
                            "note": note_str,
                            "priority": str(priority).lower(),
                        }
                    )

            logger.info(f"📋 Extracted {len(valid_items)} action items from summary")
            return valid_items

        except Exception as e:
            logger.error(f"❌ Error extracting action items: {e}")
            return []

    async def generate_meeting_title(self, summary_text: str) -> str:
        """Generate meeting title from summary text using LLM (no character limit)"""
        try:
            meeting_title_llm = ChatOpenAI(
                model=self.config.ai.summary_model,
                temperature=self.config.ai.summary_temperature,
                api_key=self.config.ai.openai_api_key,
            )

            message = HumanMessage(content=f"""
                You are a meeting assistant. Based on the meeting summary below, generate a concise and descriptive meeting title.
                
                Requirements:
                - Make it clear, descriptive, and capture the main topic/purpose of the meeting
                - Keep it concise and professional
                - No character limit - generate the most appropriate title
                
                Meeting Summary:
                \"\"\"
                {summary_text}
                \"\"\"
                
                Return ONLY the meeting title text, no other text or explanation.
            """)

            response = await meeting_title_llm.ainvoke([message])
            meeting_title = response.content.strip()

            # Remove markdown code blocks if present
            if meeting_title.startswith("```"):
                lines = meeting_title.split("\n")
                meeting_title = (
                    "\n".join(lines[1:-1]) if len(lines) > 2 else meeting_title
                )
                meeting_title = meeting_title.strip()

            logger.info(f"📋 Generated meeting title: {meeting_title}")
            return meeting_title

        except Exception as e:
            logger.error(f"❌ Error generating meeting title: {e}")
            # Return a default title if generation fails
            return "Meeting"

    async def generate_short_meeting_title(self, formatted_summary: str) -> str:
        """Generate a short meeting title (under 100 chars) from formatted summary"""
        try:
            short_title_llm = ChatOpenAI(
                model=self.config.ai.summary_model,
                temperature=self.config.ai.summary_temperature,
                api_key=self.config.ai.openai_api_key,
            )

            message = HumanMessage(content=f"""
                You are a meeting assistant. Based on the formatted meeting summary below, generate a short and concise meeting title.
                
                Requirements:
                - The title MUST be under 100 characters (strict limit)
                - Make it clear, descriptive, and capture the main topic/purpose of the meeting
                - Keep it concise and professional
                - Generate the title based on the formatted summary content
                
                Formatted Meeting Summary:
                \"\"\"
                {formatted_summary}
                \"\"\"
                
                Return ONLY the meeting title text, no other text or explanation. The title must be under 100 characters.
            """)

            response = await short_title_llm.ainvoke([message])
            short_title = response.content.strip()

            # Remove markdown code blocks if present
            if short_title.startswith("```"):
                lines = short_title.split("\n")
                short_title = "\n".join(lines[1:-1]) if len(lines) > 2 else short_title
                short_title = short_title.strip()

            # Ensure it's under 100 characters (strict requirement)
            if len(short_title) > 100:
                # If somehow exceeds, truncate but log warning
                short_title = short_title[:100]
                logger.warning(
                    f"⚠️ Short meeting title exceeded 100 chars, truncated: {short_title}"
                )

            logger.info(
                f"📋 Generated short meeting title ({len(short_title)} chars): {short_title}"
            )
            return short_title

        except Exception as e:
            logger.error(f"❌ Error generating short meeting title: {e}")
            # Return a default title if generation fails
            return "Meeting Recording"

    async def format_summary(self, summary_text: str) -> str:
        """Format summary text according to strict markdown rules using GPT-4.1"""
        try:
            formatting_llm = ChatOpenAI(
                model="gpt-4o",  # Using GPT-4.1 (gpt-4o is the latest GPT-4 model)
                temperature=0,
                api_key=self.config.ai.openai_api_key,
            )

            prompt = f"""You are a text formatter. Your task is to take the input summary text and format it according to strict markdown rules.

**CRITICAL RULES - READ CAREFULLY:**

**NO CONTENT ADDITION:** You MUST NOT add any additional content, information, or text that is not already in the input. Your purpose is ONLY to format, NOT to manipulate or enhance the text.

**ONLY FORMATTING CHANGES:** You are allowed to:
* Remove existing formatting (HTML tags, different markdown styles, etc.)
* Add formatting where it is needed but not already present (headings, lists, bolding, etc.)
* Apply consistent formatting according to the rules below

**MUST REMOVE ALL EXISTING FORMATTING:** Strip all existing formatting from the input (HTML tags, different markdown styles, emojis, etc.) and apply ONLY the formatting rules specified below.

**MANDATORY FORMATTING RULES - USE ONLY THESE RULES, NOTHING ELSE:**

**Headings:** Use ### for section headers. No HTML tags allowed. Remove all existing heading formatting (<h1>, <h2>, different markdown styles) and replace with ###.

**Bolding:** Always use **double asterisks**. No other bold markers (__, <b>, <strong>) are allowed. Remove all existing bold formatting and replace with **double asterisks**.

*Italics:* Always use *single asterisks*. No underscores or other symbols. Remove all existing italic formatting and replace with *single asterisks*.

**Lists:**
* Bulleted list → * item (asterisk + space)
* Never mix numbered and bulleted styles in the same list
* Always use proper indentation
* Remove all existing list formatting (HTML lists, different markdown styles) and replace with the rules above

**Newlines:**
* Every line break must be written as \\n
* Paragraphs → end with \\n
* Separate paragraphs and lists with one blank line → \\n
* Separate consecutive lists with one blank line → \\n
* Headers should have blank lines before and after → \\n
* Remove all actual line breaks and replace with \\n
* Normal text should be normal sentences with \\n for line breaks - no special formatting needed

**FORBIDDEN FORMATTING:**
* NO blockquotes ("> " is NOT allowed)
* NO numbered lists (# item is NOT allowed)
* NO HTML tags
* NO emojis
* NO other markdown formatting not listed above

**ONLY USE THESE FORMATTING RULES:**
1. Headings: ###
2. Bulleted lists: *
3. Bolding: **
4. Italics: *
5. Line breaks: \\n

**Normal text:** Regular sentences with \\n for line breaks. No special formatting required for normal text.

**NO EMOJIS:** Remove all emojis and special characters from the input.

**OUTPUT FORMAT:** Your response must be a SINGLE STRING with \\n characters for line breaks. Do not use actual line breaks in your output.

**EXAMPLE OF PROPERLY FORMATTED OUTPUT:**

Input: "Meeting participants: John, Sarah, Mike. Key topics: Project timeline, Budget allocation, Team assignments. Decisions: Approved Q1 budget, Assigned Sarah as lead."

Output: "### Meeting Participants\\n\\n* John\\n* Sarah\\n* Mike\\n\\n### Key Topics Discussed\\n\\n* Project timeline\\n* Budget allocation\\n* Team assignments\\n\\n### Decisions Made\\n\\n* Approved **Q1 budget**\\n* Assigned *Sarah* as lead\\n\\n### Meeting Outcome\\n\\nThe team agreed on the project timeline and budget allocation for Q1.\\n\\nAll action items have been assigned to respective team members."

**IMPORTANT:** Notice how the output is a single string with \\n for line breaks, uses ** for bolding, * for italics, ### for headings, and * for bulleted lists. Normal text is just normal sentences with \\n for line breaks. NO blockquotes, NO numbered lists, NO other formatting. The content is exactly the same as input, only formatting changed according to the rules above.

**REMEMBER:** 
- Do NOT add any content not in the input
- Do NOT change the meaning or add information
- ONLY remove existing formatting and apply the specified formatting rules
- Keep all original text content exactly as is, only change formatting

Input summary text to format:
\"\"\"
{summary_text}
\"\"\"

Return ONLY the formatted string with proper markdown formatting and \\n for line breaks. Do not include any explanation or additional text."""

            response = await formatting_llm.ainvoke([HumanMessage(content=prompt)])
            formatted_summary = response.content.strip()

            logger.info("✅ Summary formatted successfully")
            return formatted_summary

        except Exception as e:
            logger.error(f"❌ Error formatting summary: {e}")
            # Return original summary if formatting fails
            return summary_text
