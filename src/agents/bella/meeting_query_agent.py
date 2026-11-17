# import sys
# from pathlib import Path
# import dotenv
# import json
# from datetime import datetime
# from typing import Any, Dict, List, Optional, Tuple
# import re
# import numpy as np
# from sentence_transformers import SentenceTransformer
# import faiss

# src_path = Path(__file__).parent.parent.parent
# sys.path.insert(0, str(src_path))

# from livekit.agents import Agent, function_tool, ChatContext, RunContext
# from src.agents.bella.base_agent import BaseAgent
# from src.agents.bella.utils import sanitize_for_rpc, update_tool_info
# from src.agents.bella.prompts import MEETING_QUERY_SYSTEM_PROMPT
# from src.agents.bella.mock_data import mock_meeting_transcriptions

# class RAGMeetingClient:
#     def __init__(self):
#         # Initialize the embedding model
#         self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
#         self.embedding_dim = 384  # Dimension of all-MiniLM-L6-v2

#         # Initialize FAISS index for semantic search
#         self.index = faiss.IndexFlatIP(self.embedding_dim)  # Inner Product for cosine similarity
#         self.chunks = []  # Store text chunks with metadata

#         # Sample meeting data with more detailed transcripts
#         self.meetings = mock_meeting_transcriptions

#         # Initialize the RAG system
#         self._build_knowledge_base()

#     def _chunk_text(self, text: str, chunk_size: int = 200, overlap: int = 50) -> List[str]:
#         """Split text into overlapping chunks for better retrieval"""
#         words = text.split()
#         chunks = []

#         for i in range(0, len(words), chunk_size - overlap):
#             chunk_words = words[i:i + chunk_size]
#             chunks.append(' '.join(chunk_words))

#             if i + chunk_size >= len(words):
#                 break

#         return chunks

#     def _build_knowledge_base(self):
#         """Build the vector database from meeting data"""
#         print("[RAG] Building knowledge base...")

#         for meeting_id, meeting in self.meetings.items():
#             # Chunk different parts of the meeting

#             # Title and summary as one chunk
#             title_summary_chunk = f"Meeting: {meeting['title']}. Date: {meeting['date']}. Summary: {meeting['summary']}"
#             self.chunks.append({
#                 'text': title_summary_chunk,
#                 'meeting_id': meeting_id,
#                 'meeting_title': meeting['title'],
#                 'type': 'summary',
#                 'participants': meeting['participants'],
#                 'date': meeting['date']
#             })

#             # Each participant's transcript as separate chunks
#             for participant, content in meeting['transcript'].items():
#                 participant_chunks = self._chunk_text(content)
#                 for i, chunk in enumerate(participant_chunks):
#                     self.chunks.append({
#                         'text': f"{participant}: {chunk}",
#                         'meeting_id': meeting_id,
#                         'meeting_title': meeting['title'],
#                         'type': 'transcript',
#                         'participant': participant,
#                         'participants': meeting['participants'],
#                         'date': meeting['date'],
#                         'chunk_id': i
#                     })

#             # Action items as separate chunks
#             for action in meeting['action_items']:
#                 self.chunks.append({
#                     'text': f"Action item: {action}",
#                     'meeting_id': meeting_id,
#                     'meeting_title': meeting['title'],
#                     'type': 'action_item',
#                     'participants': meeting['participants'],
#                     'date': meeting['date']
#                 })

#         # Generate embeddings for all chunks
#         texts = [chunk['text'] for chunk in self.chunks]
#         embeddings = self.embedding_model.encode(texts)

#         # Normalize embeddings for cosine similarity
#         embeddings = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)

#         # Add to FAISS index
#         self.index.add(embeddings.astype('float32'))

#         print(f"[RAG] Knowledge base built with {len(self.chunks)} chunks")

#     async def semantic_search(self, query: str, top_k: int = 5, score_threshold: float = 0.25) -> List[Dict]:
#         """Perform semantic search using RAG"""
#         print(f"[RAG] Performing semantic search for: '{query}'")

#         # Encode the query
#         query_embedding = self.embedding_model.encode([query])
#         query_embedding = query_embedding / np.linalg.norm(query_embedding, axis=1, keepdims=True)

#         # Search in FAISS index
#         scores, indices = self.index.search(query_embedding.astype('float32'), top_k * 2)  # Get more to filter

#         # Filter results by score threshold and prepare response
#         results = []
#         for score, idx in zip(scores[0], indices[0]):
#             if score >= score_threshold and idx < len(self.chunks):
#                 chunk = self.chunks[idx]
#                 results.append({
#                     'chunk': chunk,
#                     'similarity_score': float(score),
#                     'meeting_id': chunk['meeting_id'],
#                     'meeting_title': chunk['meeting_title']
#                 })

#         # Remove duplicates based on meeting_id and sort by score
#         seen_meetings = set()
#         unique_results = []
#         for result in sorted(results, key=lambda x: x['similarity_score'], reverse=True):
#             # key = f"{result['meeting_id']}_{result['chunk']['type']}"
#             key = f"{result['meeting_id']}_{result.get('chunk_id', idx)}"
#             if len(unique_results) < top_k:  # Only take top_k results
#                 unique_results.append(result)

#         return unique_results[:top_k]

#     async def get_meeting_by_id(self, meeting_id: str) -> Optional[Dict]:
#         """Get a specific meeting by ID"""
#         return self.meetings.get(meeting_id)

#     def _build_rag_context(self, search_results: List[Dict]) -> str:
#         """Build context from search results for RAG"""
#         if not search_results:
#             return "No relevant meeting information found."

#         context_parts = ["Based on the following meeting information:"]

#         for i, result in enumerate(search_results, 1):
#             chunk = result['chunk']
#             score = result['similarity_score']

#             context_parts.append(f"\n{i}. From '{chunk['meeting_title']}' ({chunk['date']}):")
#             context_parts.append(f"   {chunk['text']}")
#             context_parts.append(f"   [Relevance: {score:.2f}]")

#         return '\n'.join(context_parts)

# class MeetingQueryAgent(BaseAgent):
#     def __init__(self, chat_ctx: ChatContext = None, room=None):
#         super().__init__(
#             instructions=MEETING_QUERY_SYSTEM_PROMPT,
#             chat_ctx=chat_ctx,
#         )
#         self.rag_client = RAGMeetingClient()
#         self.room = room

#         if self.room:
#             @self.room.local_participant.register_rpc_method("semantic_search_meetings")
#             async def handle_semantic_search(data):
#                 print(f"[✅ RPC RECEIVED] From: {data.caller_identity}, Payload: {data.payload}")
#                 return f"Semantic meeting search completed for: {data.payload}"

#     @function_tool()
#     async def rag_search_meetings(self, context: RunContext, query: str, max_results: int = 5) -> str:
#         """Use RAG (Retrieval-Augmented Generation) to search and answer questions about meetings using semantic search.

#         Args:
#             query: Your question or search term about meetings
#             max_results: Maximum number of relevant meetings to retrieve (default: 5)
#         """
#         print(f"[TOOL] MeetingAgent: RAG search for: '{query}'")

#         try:
#             # Perform semantic search
#             search_results = await self.rag_client.semantic_search(query, top_k=max_results)

#             if not search_results:
#                 await update_tool_info(context, tool_name="rag_search_meetings",  status="Error")
#                 return f"I couldn't find any relevant meeting information for '{query}'. Try rephrasing your question or using different keywords."

#             # Build context for RAG
#             rag_context = self.rag_client._build_rag_context(search_results)

#             with open("rag_context.json", "w") as f:
#                 json.dump(rag_context, f, indent=2)

#             # Create a RAG prompt for the LLM
#             rag_prompt = f"""
#             Context from meetings:
#             {rag_context}

#             User question: {query}

#             Based on the meeting information provided above, please answer the user's question. Be specific about which meetings and participants you're referencing. If the information is insufficient to fully answer the question, mention what additional details might be needed.
#             """

#             # Use the LLM to generate a response based on the retrieved context
#             updatable_ctx = self.chat_ctx.copy()
#             updatable_ctx.add_message(role="user", content=rag_prompt)
#             await self.update_chat_ctx(updatable_ctx)

#             # Get LLM response
#             llm_response = self.session.llm.chat(chat_ctx=self.chat_ctx)
#             response_text = ""

#             async for chunk in llm_response:
#                 if chunk.delta and chunk.delta.content:
#                     response_text += chunk.delta.content

#             await update_tool_info(self.session.userdata, "rag_search_meetings", "Success")
#             return response_text

#         except Exception as e:
#             print(f"[ERROR] MeetingAgent: RAG search failed - {e}")
#             await update_tool_info(self.session.userdata, "rag_search_meetings", "Error")
#             return f"Sorry, I encountered an error while searching your meetings: {str(e)}"

#     @function_tool()
#     async def answer_meeting_question_by_date(self, context: RunContext, meeting_date: str, question: str) -> str:
#         """
#         Answer a question based on meeting(s) held on a specific date.

#         Args:
#             meeting_date: The date of the meeting in format like "10 August 2025"
#             question: The question you want answered based on that meeting's content
#         """
#         print(f"[TOOL] MeetingAgent: Answering question for meeting date {meeting_date}: {question}")

#         try:
#             # Filter meetings by exact date match
#             matching_meetings = [
#                 (mid, mdata) for mid, mdata in self.rag_client.meetings.items()
#                 if mdata['date'].strip().lower() == meeting_date.strip().lower()
#             ]

#             if not matching_meetings:
#                 await update_tool_info(context, tool_name="answer_meeting_question_by_date", status="Error")
#                 return f"No meetings found on {meeting_date}. Please check the date format."

#             # Build chunks only from matching meetings
#             filtered_chunks = [
#                 chunk for chunk in self.rag_client.chunks
#                 if chunk['meeting_id'] in [mid for mid, _ in matching_meetings]
#             ]

#             if not filtered_chunks:
#                 return f"No content available for the meeting(s) on {meeting_date}."

#             # Run semantic search only over filtered chunks
#             query_embedding = self.rag_client.embedding_model.encode([question])
#             query_embedding = query_embedding / np.linalg.norm(query_embedding, axis=1, keepdims=True)
#             faiss_index = faiss.IndexFlatIP(self.rag_client.embedding_dim)

#             filtered_texts = [c['text'] for c in filtered_chunks]
#             filtered_embeddings = self.rag_client.embedding_model.encode(filtered_texts)
#             filtered_embeddings = filtered_embeddings / np.linalg.norm(filtered_embeddings, axis=1, keepdims=True)
#             faiss_index.add(filtered_embeddings.astype('float32'))

#             scores, indices = faiss_index.search(query_embedding.astype('float32'), k=min(5, len(filtered_chunks)))

#             results = []
#             for score, idx in zip(scores[0], indices[0]):
#                 if idx < len(filtered_chunks):
#                     results.append({
#                         'chunk': filtered_chunks[idx],
#                         'similarity_score': float(score),
#                         'meeting_id': filtered_chunks[idx]['meeting_id'],
#                         'meeting_title': filtered_chunks[idx]['meeting_title']
#                     })

#             if not results:
#                 await update_tool_info(context, tool_name="answer_meeting_question_by_date", status="Error")
#                 return f"No relevant information found for your question in the meeting on {meeting_date}."

#             rag_context = self.rag_client._build_rag_context(results)

#             rag_prompt = f"""
#     Context from meeting(s) on {meeting_date}:
#     {rag_context}

#     User question: {question}

#     Please answer the user's question based only on the above meeting content. Be specific about which participants are referenced.
#     """
#             updatable_ctx = self.chat_ctx.copy()
#             updatable_ctx.add_message(role="user", content=rag_prompt)
#             await self.update_chat_ctx(updatable_ctx)

#             llm_response = self.session.llm.chat(chat_ctx=self.chat_ctx)
#             response_text = ""
#             async for chunk in llm_response:
#                 if chunk.delta and chunk.delta.content:
#                     response_text += chunk.delta.content

#             await update_tool_info(self.session.userdata, "answer_meeting_question_by_date", "Success")
#             return response_text

#         except Exception as e:
#             print(f"[ERROR] MeetingAgent: Failed to answer question by date - {e}")
#             await update_tool_info(context, tool_name="answer_meeting_question_by_date", status="Error")
#             return f"Sorry, I encountered an error: {str(e)}"


#     @function_tool()
#     async def handle_todo_request(self, context: RunContext):
#         """Handles any user requests related to their to-do list, tasks, or task management."""
#         return await self._transfer_to_agent("todo", context)

#     @function_tool()
#     async def handle_calendar_request(self, context: RunContext):
#         """Handles any user requests related to their calendar, schedule, or appointments."""
#         return await self._transfer_to_agent("calendar", context)
