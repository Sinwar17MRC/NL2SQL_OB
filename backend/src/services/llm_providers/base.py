from abc import ABC, abstractmethod

class LLMProvider(ABC):
    """
    Abstract Base Class defining the contract for all LLM providers.
    Ensures that any provider we create has a consistent interface.
    """
    
    @abstractmethod
    async def agenerate(self, prompt: str, system_prompt: str, use_json_mode: bool = False, model: str = None) -> str:
        """
        Asynchronously generates a text response based on a prompt.

        Args:
            prompt (str): The main user prompt.
            system_prompt (str): Instructions for the model's persona and behavior.
            use_json_mode (bool): If True, instruct the model to output valid JSON.
            model (str, optional): The specific model version to use. Defaults to the provider's choice.

        Returns:
            str: The LLM's generated text response.
        """
        pass