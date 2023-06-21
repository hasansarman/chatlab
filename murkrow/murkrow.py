"""Simple chatterbox."""

import json
import logging
from typing import Callable, List, Optional, Union

import openai
from pydantic import BaseModel

from murkrow.registry import FunctionRegistry

from .display import ChatFunctionDisplay, Markdown
from .messaging import Message, assistant, assistant_function_call, function_result, human

logger = logging.getLogger(__name__)


class Session:
    """A simple chat class that uses OpenAI's Chat API.

    Messages stream in as they are generated by the API.

    History is tracked and can be used to continue a conversation.

    Args:
        initial_context (str | Message): The initial context for the conversation.


    >>> from murkrow import Session
    >>> session = Session("Hello!")
    >>> session.chat("How are you?")
    Hello!
    How are you?
    >>> .chat("I'm fine, thanks.")
    Nice to hear!

    """

    messages: List[Message]
    model: str
    function_registry: FunctionRegistry
    auto_continue: bool

    def __init__(
        self,
        *initial_context: Union[Message, str],
        model="gpt-3.5-turbo-0613",
        function_registry: Optional[FunctionRegistry] = None,
        auto_continue: bool = True,
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

        if function_registry is None:
            self.function_registry = FunctionRegistry()
        else:
            self.function_registry = function_registry

    def chat(self, *messages: Union[Message, str], auto_continue: Optional[bool] = None):
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

        chat_function_display = None

        in_function = False
        # We can replace in_function with chat_function_display is not None

        for result in resp:  # Go through the results of the stream
            # TODO: Move this setup back into deltas
            choice = result['choices'][0]  # Get the first choice, since we're not doing bulk

            if 'delta' in choice:  # If there is a delta in the result
                delta = choice['delta']
                if 'content' in delta and delta['content'] is not None:  # If the delta contains content
                    mark.append(delta['content'])  # Extend the markdown with the content

                elif 'function_call' in delta:  # If the delta contains a function call
                    # Previous message finished
                    if not in_function:
                        # Wrap up the previous assistant message
                        if mark.message.strip() != "":
                            self.messages.append(assistant(mark.message))
                            mark = Markdown()
                            mark.display()

                        in_function = True

                    function_call = delta['function_call']
                    if 'name' in function_call:
                        chat_function_display = ChatFunctionDisplay(function_call["name"])
                        chat_function_display.display()

                    if 'arguments' in function_call:
                        if chat_function_display is None:
                            raise ValueError("Function arguments provided without function name")
                        chat_function_display.append_arguments(function_call['arguments'])

            if 'finish_reason' in choice and choice['finish_reason'] == "function_call":
                if chat_function_display is None:
                    raise ValueError("Function call finished without function name")

                function_name = chat_function_display.function_name
                function_args = chat_function_display.function_args

                if function_name and function_args and function_name in self.function_registry:
                    chat_function_display.set_state("Running")
                    self.messages.append(assistant_function_call(name=function_name, arguments=function_args))

                    # Evaluate the arguments as a JSON
                    arguments = json.loads(function_args)

                    # Execute the function and get the result
                    output = self.function_registry.call(function_name, arguments)

                    repr_llm = repr(output)

                    chat_function_display.append_result(repr_llm)
                    chat_function_display.set_state("Ran")
                    chat_function_display.set_finished()

                    self.messages.append(function_result(name=function_name, content=repr_llm))

                    # Reset to no function display for the next call
                    chat_function_display = None

                    in_function = False
            elif 'finish_reason' in choice and choice['finish_reason'] is not None:
                if not in_function:
                    # Wrap up the previous assistant
                    self.messages.append(assistant(mark.message))

                if 'max_tokens' in choice['finish_reason']:
                    mark.append("\n...MAX TOKENS REACHED...\n")

        # In priority order:
        #
        # `auto_continue` argument
        # `self.auto_continue`
        #
        # If `auto_continue` is False, then `self.auto_continue` is ignored
        continuing = False

        if auto_continue is not None:
            continuing = auto_continue
        elif self.auto_continue:
            continuing = True

        if continuing and self.messages[-1]['role'] == 'function':
            # Automatically let the LLM continue from our function result
            self.chat()

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
            parameters_model (BaseModel): The pydantic model to use for parameters.

        """
        full_schema = self.function_registry.register(function, parameters_model, json_schema)

        logger.debug("Created function with schema:")
        logger.debug(json.dumps(full_schema, indent=2))
