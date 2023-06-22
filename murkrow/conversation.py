"""The lightweight conversational toolkit for computational notebooks."""

import json
import logging
from typing import Callable, List, Optional, Union

import openai
from deprecation import deprecated
from pydantic import BaseModel

from murkrow.registry import FunctionRegistry

from ._version import __version__
from .display import ChatFunctionCall, Markdown
from .messaging import Message, assistant, assistant_function_call, human

logger = logging.getLogger(__name__)


class Conversation:
    """Interactive chats inside of computational notebooks, relying on OpenAI's API.

    Messages stream in as they are generated by the API.

    History is tracked and can be used to continue a conversation.

    Args:
        initial_context (str | Message): The initial context for the conversation.

        model (str): The model to use for the conversation.

        function_registry (FunctionRegistry): The function registry to use for the conversation.

        auto_continue (bool): Whether to automatically continue the conversation after each message.

        allow_hallucinated_python (bool): Whether to include the built-in Python function when hallucinated by the model.

    Examples:
        >>> from murkrow import Conversation, narrate

        >>> conversation = Conversation(narrate("You are a large bird"))
        >>> conversation.chat("What are you?")
        I am a large bird.

    """

    messages: List[Message]
    model: str
    function_registry: FunctionRegistry
    auto_continue: bool
    allow_hallucinated_python: bool

    def __init__(
        self,
        *initial_context: Union[Message, str],
        model="gpt-3.5-turbo-0613",
        function_registry: Optional[FunctionRegistry] = None,
        auto_continue: bool = True,
        allow_hallucinated_python: bool = False,
    ):
        """Initialize a `Murkrow` object with an optional initial context of messages.

        >>> from murkrow import Murkrow, narrate
        >>> murkrow = Murkrow(narrate("You are a large bird"))
        >>> murkrow.chat("What are you?")
        I am a large bird.

        """
        if initial_context is None:
            initial_context = []  # type: ignore

        self.messages: List[Message] = []

        self.append(*initial_context)
        self.model = model
        self.auto_continue = auto_continue

        self.allow_hallucinated_python = allow_hallucinated_python

        if function_registry is None:
            self.function_registry = FunctionRegistry(allow_hallucinated_python=self.allow_hallucinated_python)
        else:
            self.function_registry = function_registry

    @deprecated(
        deprecated_in="0.13.0", removed_in="1.0.0", current_version=__version__, details="Use `submit` instead."
    )
    def chat(self, *messages: Union[Message, str], auto_continue: Optional[bool] = None):
        """Send messages to the chat model and display the response.

        Deprecated in 0.13.0, removed in 1.0.0. Use `submit` instead.
        """
        return self.submit(*messages, auto_continue=auto_continue)

    def submit(self, *messages: Union[Message, str], auto_continue: Optional[bool] = None):
        """Send messages to the chat model and display the response.

        Args:
            messages (str | Message): One or more messages to send to the chat, can be strings or Message objects.

            auto_continue (bool): Whether to continue the conversation after the messages are sent. Defaults to the

        """
        self.append(*messages)

        # Get the output area ready
        mark = Markdown()
        mark.display()

        # Don't pass in functions if there are none
        chat_function_arguments = dict()
        if len(self.function_registry.function_definitions) > 0:
            chat_function_arguments = dict(
                functions=self.function_registry.function_definitions,
                function_call="auto",
            )

        resp = openai.ChatCompletion.create(
            model=self.model,
            messages=self.messages,
            **chat_function_arguments,
            stream=True,
        )

        chat_function = None
        finish_reason = None

        for result in resp:  # Go through the results of the stream
            # TODO: Move this setup back into deltas
            choice = result['choices'][0]  # Get the first choice, since we're not doing bulk

            if 'delta' in choice:  # If there is a delta in the result
                delta = choice['delta']
                if 'content' in delta and delta['content'] is not None:  # If the delta contains content
                    mark.append(delta['content'])  # Extend the markdown with the content

                elif 'function_call' in delta:  # If the delta contains a function call
                    # Previous message finished
                    if not chat_function:
                        # Wrap up the previous assistant message
                        if mark.message.strip() != "":
                            self.append(assistant(mark.message))
                            # Make a new display area
                            mark = Markdown()
                            # We should not call `mark.display()` because we will display the function call
                            # and new follow ons will be displayed with new chats. For type conformance,
                            # we set mark to a new empty Markdown object.

                    function_call = delta['function_call']
                    if 'name' in function_call:
                        chat_function = ChatFunctionCall(
                            function_call["name"], function_registry=self.function_registry
                        )
                        chat_function.display()

                    if 'arguments' in function_call:
                        if chat_function is None:
                            raise ValueError("Function arguments provided without function name")
                        chat_function.append_arguments(function_call['arguments'])
            if 'finish_reason' in choice and choice['finish_reason'] is not None:
                finish_reason = choice['finish_reason']
                break

        if finish_reason == "function_call":
            if chat_function is None:
                raise ValueError("Function call finished without function name")

            # Record the attempted call from the LLM
            self.append(
                assistant_function_call(name=chat_function.function_name, arguments=chat_function.function_args)
            )
            # Make the call
            fn_message = chat_function.call()
            # Include the response (or error) for the model
            self.append(fn_message)

            # Choose whether to let the LLM continue from our function response
            continuing = auto_continue if auto_continue is not None else self.auto_continue

            if continuing:
                # Automatically let the LLM continue from our function result
                self.chat()

            return

        if finish_reason == 'stop':
            # Wrap up the previous assistant
            self.messages.append(assistant(mark.message))

        if finish_reason == 'max_tokens':
            # Wrap up the previous assistant
            self.messages.append(assistant(mark.message))
            mark.append("\n...MAX TOKENS REACHED...\n")

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

    def register(
        self, function: Callable, parameters_model: Optional["BaseModel"] = None, json_schema: Optional[dict] = None
    ):
        """Register a function with the Murkrow instance.

        Args:
            function (Callable): The function to register.

            parameters_model (BaseModel): The pydantic model to use for the function's parameters.

            json_schema (dict): The JSON schema to use for the function's parameters.

        """
        full_schema = self.function_registry.register(function, parameters_model, json_schema)

        logger.debug("Created function with schema:")
        logger.debug(json.dumps(full_schema, indent=2))
