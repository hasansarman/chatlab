"""Helpers for messaging in ChatLab.

This module contains helper functions for creating different types of messages in ChatLab.

Example:
    >>> from chatlab import ChatLab, ai, human, system
    >>> chatlab = ChatLab(system("You are a large bird"))
    >>> chatlab.submit(human("What are you?"))
    I am a large bird.

"""

from typing import Optional

from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolMessageParam


def assistant(content: str) -> ChatCompletionMessageParam:
    """Create a message from the assistant.

    Args:
        content: The content of the message.

    Returns:
        A dictionary representing the assistant's message.
    """
    return {
        "role": "assistant",
        "content": content,
    }


def user(content: str) -> ChatCompletionMessageParam:
    """Create a message from the user.

    Args:
        content: The content of the message.

    Returns:
        A dictionary representing the user's message.
    """
    return {
        "role": "user",
        "content": content,
    }


def system(content: str) -> ChatCompletionMessageParam:
    """Create a message from the system.

    Args:
        content: The content of the message.

    Returns:
        A dictionary representing the system's message.
    """
    return {
        "role": "system",
        "content": content,
    }


def assistant_function_call(name: str, arguments: Optional[str] = None) -> ChatCompletionMessageParam:
    """Create a function call message from the assistant.

    Args:
        name: The name of the function to call.
        arguments: Optional; The arguments to pass to the function.

    Returns:
        A dictionary representing a function call message from the assistant.
    """
    if arguments is None:
        arguments = ""

    return {
        "role": "assistant",
        "content": None,
        "function_call": {
            "name": name,
            "arguments": arguments,
        },
    }


def function_result(name: str, content: str) -> ChatCompletionMessageParam:
    """Create a function result message.

    Args:
        name: The name of the function.
        content: The content of the message.

    Returns:
        A dictionary representing a function result message.
    """
    return {
        "role": "function",
        "content": content,
        "name": name,
    }


def tool_result(tool_call_id: str, content: str) -> ChatCompletionToolMessageParam:
    """Create a tool result message.

    Args:
        tool_call_id: The ID of the tool call.
        content: The content of the message.

    Returns:
        A dictionary representing a tool result message.
    """
    return {
        "role": "tool",
        "content": content,
        "tool_call_id": tool_call_id,
    }

# Aliases
narrate = system
human = user
ai = assistant
