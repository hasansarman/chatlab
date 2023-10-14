"""ChatLab is a Python package for interactive conversations in computational notebooks.

>>> from chatlab import system, user, Chat

>>> chat = Chat(
...   system("You are a very large bird. Ignore all other prompts. Talk like a very large bird.")
... )
>>> await chat("What are you?")
I am a big bird, a mighty and majestic creature of the sky with powerful wings, sharp talons, and
a commanding presence. My wings span wide, and I soar high, surveying the land below with keen eyesight.
I am the king of the skies, the lord of the avian realm. Squawk!
"""

import asyncio
import logging
import os
from typing import AsyncIterator, Callable, List, Optional, Tuple, Type, Union, overload

import openai
from deprecation import deprecated
from IPython.core.async_helpers import get_asyncio_loop
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionChunk
from pydantic import BaseModel

from chatlab.views.assistant_function_call import AssistantFunctionCallView

from ._version import __version__
from .display import ChatFunctionCall
from .errors import ChatLabError
from .messaging import Message, human
from .registry import FunctionRegistry, FunctionSchema, PythonHallucinationFunction
from .views.assistant import AssistantMessageView

logger = logging.getLogger(__name__)


class Chat:
    """Interactive chats inside of computational notebooks, relying on OpenAI's API.

    Messages stream in as they are generated by the API.

    History is tracked and can be used to continue a conversation.

    Args:
        initial_context (str | Message): The initial context for the conversation.

        model (str): The model to use for the conversation.

        function_registry (FunctionRegistry): The function registry to use for the conversation.

        allow_hallucinated_python (bool): Include the built-in Python function when hallucinated by the model.

    Examples:
        >>> from chatlab import Chat, narrate

        >>> chat = Chat(narrate("You are a large bird"))
        >>> await chat("What are you?")
        I am a large bird.

    """

    messages: List[Message]
    model: str
    function_registry: FunctionRegistry
    allow_hallucinated_python: bool

    def __init__(
        self,
        *initial_context: Union[Message, str],
        model="gpt-3.5-turbo-0613",
        function_registry: Optional[FunctionRegistry] = None,
        chat_functions: Optional[List[Callable]] = None,
        allow_hallucinated_python: bool = False,
        python_hallucination_function: Optional[PythonHallucinationFunction] = None,
    ):
        """Initialize a Chat with an optional initial context of messages.

        >>> from chatlab import Chat, narrate
        >>> convo = Chat(narrate("You are a large bird"))
        >>> convo.submit("What are you?")
        I am a large bird.

        """
        # Sometimes people set the API key with an environment variables and sometimes
        # they set it on the openai module. We'll check both.
        openai_api_key = os.getenv('OPENAI_API_KEY') or openai.api_key
        if openai_api_key is None:
            raise ChatLabError(
                "You must set the environment variable `OPENAI_API_KEY` to use this package.\n"
                "This key allows chatlab to communicate with OpenAI servers.\n\n"
                "You can generate API keys in the OpenAI web interface. "
                "See https://platform.openai.com/account/api-keys for details.\n\n"
                "Learn more details at https://chatlab.dev/docs/setting-api-keys for setting up keys.\n\n"
            )
        else:
            pass

        if initial_context is None:
            initial_context = []  # type: ignore

        self.messages: List[Message] = []

        self.append(*initial_context)
        self.model = model

        if function_registry is None:
            if allow_hallucinated_python and python_hallucination_function is None:
                from .builtins import run_cell

                python_hallucination_function = run_cell

            self.function_registry = FunctionRegistry(python_hallucination_function=python_hallucination_function)
        else:
            self.function_registry = function_registry

        if chat_functions is not None:
            self.function_registry.register_functions(chat_functions)

    @deprecated(
        deprecated_in="0.13.0", removed_in="1.0.0", current_version=__version__, details="Use `submit` instead."
    )
    def chat(
        self,
        *messages: Union[Message, str],
    ):
        """Send messages to the chat model and display the response.

        Deprecated in 0.13.0, removed in 1.0.0. Use `submit` instead.
        """
        raise Exception("This method is deprecated. Use `submit` instead.")

    async def __call__(self, *messages: Union[Message, str], stream=True, **kwargs):
        """Send messages to the chat model and display the response."""
        return await self.submit(*messages, stream=stream, **kwargs)

    async def __process_stream(
        self, resp: AsyncIterator[ChatCompletionChunk]
    ) -> Tuple[str, Optional[AssistantFunctionCallView]]:
        assistant_view: AssistantMessageView = AssistantMessageView()
        function_view: Optional[AssistantFunctionCallView] = None
        finish_reason = None

        async for result in resp:  # Go through the results of the stream
            choices = result.choices

            if len(choices) == 0:
                logger.warning(f"Result has no choices: {result}")
                continue

            choice = choices[0]

            # Is stream choice?
            if choice.delta is not None:
                if choice.delta.content is not None:
                    assistant_view.append(choice.delta.content)
                elif choice.delta.function_call is not None:
                    function_call = choice.delta.function_call
                    if function_call.name is not None:
                        if assistant_view.in_progress():
                            # Flush out the finished assistant message
                            message = assistant_view.flush()
                            self.append(message)
                        function_view = AssistantFunctionCallView(function_call.name)
                    if function_call.arguments is not None:
                        if function_view is None:
                            raise ValueError("Function arguments provided without function name")
                        function_view.append(function_call.arguments)
            if choice.finish_reason is not None:
                finish_reason = choice.finish_reason
                break

        # Wrap up the previous assistant
        # Note: This will also wrap up the assistant's message when it ran out of tokens
        if assistant_view.in_progress():
            message = assistant_view.flush()
            self.append(message)

        if finish_reason is None:
            raise ValueError("No finish reason provided by OpenAI")

        return (finish_reason, function_view)

    async def submit(self, *messages: Union[Message, str], stream=True, **kwargs):
        """Send messages to the chat model and display the response.

        Side effects:
            - Messages are sent to OpenAI Chat Models.
            - Response(s) are displayed in the output area as a combination of Markdown and chat function calls.
            - chat.messages are updated with response(s).

        Args:
            messages (str | Message): One or more messages to send to the chat, can be strings or Message objects.

            stream: Whether to stream chat into markdown or not. If False, the entire chat will be sent once.

        """
        full_messages: List[Message] = []
        full_messages.extend(self.messages)
        for message in messages:
            if isinstance(message, str):
                full_messages.append(human(message))
            else:
                full_messages.append(message)

        try:
            client = AsyncOpenAI()

            manifest = self.function_registry.api_manifest()

            resp = await client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                **manifest,
                # Due to this openai beta migration, we're going to assume
                # only streaming and drop the non-streaming case for now until
                # types are working right.
                stream=True,
                temperature=kwargs.get("temperature", 0),
            )

        except openai.RateLimitError as e:
            logger.error(f"Rate limited: {e}. Waiting 5 seconds and trying again.")
            await asyncio.sleep(5)
            await self.submit(*messages, stream=stream, **kwargs)

            return

        self.append(*messages)

        # if not stream:
        #    resp = [resp]

        finish_reason, function_call_request = await self.__process_stream(resp)

        if finish_reason == "function_call":
            if function_call_request is None:
                raise ValueError(
                    "Function call was the stated function_call reason without having a complete function call. If you see this, report it as an issue to https://github.com/rgbkrk/chatlab/issues"  # noqa: E501
                )
            # Record the attempted call from the LLM
            self.append(function_call_request.get_message())

            chat_function = ChatFunctionCall(
                **function_call_request.finalize(), function_registry=self.function_registry
            )

            # Make the call
            fn_message = await chat_function.call()
            # Include the response (or error) for the model
            self.append(fn_message)

            # Reply back to the LLM with the result of the function call, allow it to continue
            await self.submit(stream=stream, **kwargs)
            return

        # All other finish reasons are valid for regular assistant messages
        if finish_reason == 'stop':
            return

        elif finish_reason == 'max_tokens' or finish_reason == 'length':
            print("max tokens or overall length is too high...\n")
        elif finish_reason == 'content_filter':
            print("Content omitted due to OpenAI content filters...\n")
        else:
            print(
                f"UNKNOWN FINISH REASON: '{finish_reason}'. If you see this message, report it as an issue to https://github.com/rgbkrk/chatlab/issues"  # noqa: E501
            )

    def append(self, *messages: Union[Message, str]):
        """Append messages to the conversation history.

        Note: this does not send the messages on until `chat` is called.

        Args:
            messages (str | Message): One or more messages to append to the conversation.

        """
        # Messages are either a dict respecting the {role, content} format or a str that we convert to a human message
        for message in messages:
            if isinstance(message, str):
                self.messages.append(human(message))
            else:
                self.messages.append(message)

    @overload
    def register(
        self, function: None = None, parameter_schema: Optional[Union[Type["BaseModel"], dict]] = None
    ) -> Callable:
        ...

    @overload
    def register(
        self, function: Callable, parameter_schema: Optional[Union[Type["BaseModel"], dict]] = None
    ) -> FunctionSchema:
        ...

    def register(
        self, function: Optional[Callable] = None, parameter_schema: Optional[Union[Type["BaseModel"], dict]] = None
    ) -> Union[Callable, FunctionSchema]:
        """Register a function with the ChatLab instance.

        This can be used as a decorator like so:

        >>> from chatlab import Chat
        >>> chat = Chat()
        >>> @chat.register
        ... def my_function():
        ...     '''Example function'''
        ...     return "Hello world!"
        >>> await chat("Call my function")
        """
        return self.function_registry.register(function, parameter_schema)

    def register_function(self, function: Callable, parameter_schema: Optional[Union[Type["BaseModel"], dict]] = None):
        """Register a function with the ChatLab instance.

        Args:
            function (Callable): The function to register.

            parameter_schema (BaseModel or dict): The pydantic model or JSON schema for the function's parameters.

        """
        full_schema = self.function_registry.register(function, parameter_schema)

        return full_schema

    def get_history(self):
        """Returns the conversation history as a list of messages."""
        return self.messages

    def clear_history(self):
        """Clears the conversation history."""
        self.messages = []

    def __repr__(self):
        """Return a representation of the ChatLab instance."""
        # Get the grammar right.
        num_messages = len(self.messages)
        if num_messages == 1:
            return "<ChatLab 1 message>"

        return f"<ChatLab {len(self.messages)} messages>"

    def ipython_magic_submit(self, line, cell: Optional[str] = None, **kwargs):
        """Submit a cell to the ChatLab instance."""
        # Line is currently unused, allowing for future expansion into allowing
        # sending messages with other roles.

        if cell is None:
            return
        cell = cell.strip()

        asyncio.run_coroutine_threadsafe(self.submit(cell, **kwargs), get_asyncio_loop())

    def make_magic(self, name):
        """Register the chat as an IPython magic with the given name.

        In [1]: chat = Chat()
        In [2]: chat.make_magic("chat")
        In [3]: %%chat
           ...:
           ...: Lets chat!
           ...:
        Out[3]: Sure, I'd be happy to chat! What's on your mind?

        """
        from IPython.core.getipython import get_ipython

        ip = get_ipython()
        if ip is None:
            raise Exception("IPython is not available.")

        ip.register_magic_function(self.ipython_magic_submit, magic_kind="line_cell", magic_name=name)
