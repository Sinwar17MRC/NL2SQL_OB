import logging
import json
import asyncio
from typing import List, Dict, Any, Coroutine, Tuple

# Import the provider classes
from .llm_providers.openai import OpenAIProvider
from .llm_providers.gemini import GoogleProvider

logger = logging.getLogger(__name__)

class LLMService:
    """
    The definitive, model-agnostic LLM service. It orchestrates calls to
    different providers for specific tasks, adhering to an efficient,
    one-call-per-cluster strategy, and includes robust JSON parsing.
    """
    def __init__(self):
        self.providers = {}
        try: 
            self.providers['openai'] = OpenAIProvider()
        except Exception as e: logger.warning(f"OpenAI provider disabled: {e}")
        try: 
            self.providers['google'] = GoogleProvider()
        except Exception as e: logger.warning(f"Google provider disabled: {e}")

        if not self.providers: raise RuntimeError("No LLM providers could be initialized.")

    async def _make_api_call(self, provider_name: str, **kwargs) -> str:
        """Routes a request to the specified provider."""
        if provider_name not in self.providers:
            raise ValueError(f"Provider '{provider_name}' is not available.")
        return await self.providers[provider_name].agenerate(**kwargs)

    def _robust_json_parser(self, raw_response: str) -> Dict:
        """
        A resilient parser for LLM JSON responses. It cleans the string
        before attempting to parse it.
        """
        logger.debug(f"Attempting to parse raw LLM response: {raw_response[:60]}...")
        # Find the start of the JSON object
        start_brace = raw_response.find('{')
        # Find the end of the JSON object (search from the end)
        end_brace = raw_response.rfind('}')
        
        if start_brace == -1 or end_brace == -1:
            logger.error("No JSON object found in the LLM response.")
            return {}
            
        json_substring = raw_response[start_brace : end_brace + 1]
        
        try:
            return json.loads(json_substring)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse cleaned JSON substring. Error: {e}")
            logger.debug(f"Problematic JSON string: {json_substring}")
            return {} 

    # --- Public Methods for the Onboarding Pipeline ---

    async def generate_descriptions_for_cluster(self, cluster_schema_data: List[Dict[str, Any]], metadata: Dict[str, Any]) -> Dict[str, str]:
        """
        Generates descriptions for all tables in a related cluster via a single API call.
        """
        PROVIDER = "google" # Use Gemini for descriptions
        prompt = self._build_cluster_prompt(cluster_schema_data, metadata)
        system_prompt = "You are a helpful database analyst. Your entire response must be a single, valid JSON object and nothing else."
        
        logger.info(f"Generating descriptions for cluster '{metadata['cluster_id']}' ({metadata['size']} tables) in a single call.")
        try:
            response_text = await self._make_api_call(
                PROVIDER, 
                prompt=prompt,
                system_prompt=system_prompt, 
                use_json_mode=True
            )
            descriptions_map = self._robust_json_parser(response_text)
            logger.info(f"Successfully parsed {len(descriptions_map)} descriptions for cluster '{metadata['cluster_id']}'.")
            return descriptions_map
        except Exception as e:
            logger.error(f"API call failed for cluster '{metadata['cluster_id']}': {e}")
            return {}

    async def generate_descriptions_for_singleton_batch(self, batch_schema_data: List[Dict[str, Any]]) -> Dict[str, str]:
        """
        Generates descriptions for a batch of unrelated singleton tables in a single API call.
        """
        PROVIDER = "google" # Use Gemini for descriptions
        prompt = self._build_singleton_batch_prompt(batch_schema_data)
        system_prompt = "You are a helpful database analyst. Your entire response must be a single, valid JSON object and nothing else."

        logger.info(f"Generating descriptions for a batch of {len(batch_schema_data)} singleton tables.")
        try:
            response_text = await self._make_api_call(
                PROVIDER,
                prompt=prompt,
                system_prompt=system_prompt,
                use_json_mode=True
            )
            descriptions_map = self._robust_json_parser(response_text)
            logger.info(f"Successfully parsed {len(descriptions_map)} descriptions for the singleton batch.")
            return descriptions_map
        except Exception as e:
            logger.error(f"API call failed for singleton batch: {e}")
            return {}

    # --- Public Method for the Live Query Pipeline ---

    async def generate_sql_query(self, user_question: str, context_schema: str) -> str:
        """
        Uses GPT-4 for high-quality SQL generation.
        """
        PROVIDER = "openai"
        prompt = self._build_sql_generation_prompt(user_question, context_schema)
        system_prompt = "You are an expert T-SQL developer. Generate a single, valid T-SQL query based on the provided schema and user question. Your entire response must be only the SQL query and nothing else."

        try:
            sql_query = await self._make_api_call(PROVIDER, prompt=prompt, system_prompt=system_prompt)
            # Basic cleaning for SQL: remove markdown and trailing semicolons
            return sql_query.strip().removeprefix("```sql").removesuffix("```").strip().rstrip(';')
        except Exception as e:
            logger.error(f"API call failed for SQL generation: {e}")
            return f"-- ERROR: Could not generate SQL query. Reason: {e}"


    # --- Prompt Engineering Methods ---

    def _build_cluster_prompt(self, cluster_schema_data: List[Dict[str, Any]], metadata: Dict[str, Any]) -> str:
        """
        Constructs a detailed prompt to generate a specific description FOR EACH TABLE
        within a related cluster, leveraging the cluster's context.
        """
        prompt = (
            "You are an expert database architect. Your task is to analyze a group of related database tables "
            "and generate a concise, business-oriented description for EACH table individually. "
            "Use the context of the surrounding tables to make each description more accurate and insightful."
        )
        
        prompt += "\n\n--- Context: Cluster Analysis ---\n"
        prompt += f"You are analyzing a group of {metadata['size']} tables that form a '{metadata['density_label']}' density cluster. "
        
        if metadata.get('central_table'):
            prompt += f"The most central or important table in this group appears to be '{metadata['central_table']}'. "
            prompt += "The other tables likely support or relate to it."

        prompt += "\n\n--- Table Schemas ---\n"
        for table_schema in cluster_schema_data:
            table_id = table_schema['full_table_id']
            prompt += f"\nTable: {table_id}\n"
            if table_schema.get('business_hints'):
                prompt += f"  Business Hints: {', '.join(table_schema['business_hints'])}\n"
            prompt += "  Columns:\n"
            for col in table_schema['columns']:
                pk_note = " (Primary Key)" if col['name'] in table_schema.get('primary_key', []) else ""
                notes = f" [{col['notes']}]" if col.get('notes') else ""
                prompt += f"    - {col['name']} ({col['type']}){pk_note}{notes}\n"
            if table_schema.get('foreign_keys'):
                prompt += "  Relationships:\n"
                for fk in table_schema['foreign_keys']:
                    referred_table = f"{fk.get('referred_schema', '')}.{fk.get('referred_table', '')}"
                    prompt += f"    - Connects to '{referred_table}' via column(s) {fk['constrained_columns']}\n"

        prompt += (
            "\n\n--- YOUR TASK ---\n"
            "Based on the schemas and context provided, generate a description for EACH of the tables listed above. "
            "The description for each table should be a single, concise sentence explaining its primary business purpose. "
            "Your entire response MUST be a single, valid JSON object where each key is the full table name (e.g., 'dbo.Orders') "
            "and the value is its corresponding one-sentence description. Do not include any text or markdown outside of the JSON object."
        )
        
        return prompt
    
    def _build_singleton_batch_prompt(self, batch_schema_data: List[Dict[str, Any]]) -> str:
        """
        Constructs a prompt for describing a batch of individual, unrelated tables.
        """
        prompt = (
            "You are an expert database architect. Your task is to analyze a batch of database tables "
            "and generate a concise, business-oriented description for EACH table individually."
        )

        prompt += "\n\n--- Table Schemas ---\n"
        for table_schema in batch_schema_data:
            table_id = table_schema['full_table_id']
            prompt += f"\nTable: {table_id}\n"
            if table_schema.get('business_hints'):
                prompt += f"  Business Hints: {', '.join(table_schema['business_hints'])}\n"
            prompt += "  Columns:\n"
            for col in table_schema['columns']:
                pk_note = " (Primary Key)" if col['name'] in table_schema.get('primary_key', []) else ""
                notes = f" [{col['notes']}]" if col.get('notes') else ""
                prompt += f"    - {col['name']} ({col['type']}){pk_note}{notes}\n"
            if table_schema.get('foreign_keys'):
                prompt += "  Relationships:\n"
                for fk in table_schema['foreign_keys']:
                    referred_table = f"{fk.get('referred_schema', '')}.{fk.get('referred_table', '')}"
                    prompt += f"    - Connects to '{referred_table}' via column(s) {fk['constrained_columns']}\n"

        prompt += (
            "\n\n--- YOUR TASK ---\n"
            "Based on the schemas provided, generate a description for EACH of the tables listed above. "
            "The description for each table should be a single, concise sentence explaining its primary business purpose. "
            "Your entire response MUST be a single, valid JSON object where each key is the full table name (e.g., 'dbo.Users') "
            "and the value is its corresponding one-sentence description. Do not include any text or markdown outside of the JSON object."
        )
        
        return prompt
        
    def _build_sql_generation_prompt(self, user_question: str, context_schema: str) -> str:
        """
        Constructs the prompt for the live SQL generation task.
        """
        prompt = f"""
Based on the database schema context below, write a single, syntactically correct T-SQL query to answer the user's question.
Pay close attention to the relationships between tables to construct the necessary JOINs.
Use the column notes to handle special data types correctly, for example by using `.ToString()` on unsupported types if they are requested.

--- Schema Context ---
{context_schema}

--- User Question ---
{user_question}

--- T-SQL Query ---
"""
        return prompt