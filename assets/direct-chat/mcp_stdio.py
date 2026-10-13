"""MCP stdio endpoint: discover locally, relay tool calls to the direct runner."""
import argparse
import json
from pathlib import Path
import socket
import sys

MAX_LINE = 4 * 1024 * 1024


def tool_call(socket_path, name, arguments, timeout):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(socket_path)
        connection.sendall((json.dumps(dict(name=name, arguments=arguments), ensure_ascii=False) + '\n').encode('utf-8'))
        with connection.makefile('r', encoding='utf-8') as handle:
            line = handle.readline(MAX_LINE + 1)
        if not line.endswith('\n') or len(line.encode('utf-8')) > MAX_LINE:
            raise ValueError('invalid_response')
        result = json.loads(line)
        if not isinstance(result, dict) or not isinstance(result.get('content'), list):
            raise ValueError('invalid_response')
        return result


def handle(message, tools, relay):
    if not isinstance(message, dict) or 'id' not in message:
        return None
    response = {'jsonrpc': '2.0', 'id': message['id']}
    method = message.get('method')
    params = message.get('params') or {}
    if not isinstance(params, dict):
        response['error'] = {'code': -32602, 'message': 'Invalid parameters'}
    elif method == 'initialize':
        response['result'] = {'protocolVersion': params.get('protocolVersion', '2024-11-05'), 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'laptop', 'version': '1.0.0'}}
    elif method == 'ping':
        response['result'] = {}
    elif method == 'tools/list':
        response['result'] = {'tools': tools}
    elif method == 'tools/call':
        name, arguments = params.get('name'), params.get('arguments', {})
        if name not in {tool['name'] for tool in tools} or not isinstance(arguments, dict):
            response['error'] = {'code': -32602, 'message': 'Unknown laptop tool or invalid arguments'}
        else:
            try:
                response['result'] = relay(name, arguments)
            except (OSError, ValueError, TypeError):
                response['result'] = {'content': [{'type': 'text', 'text': 'Laptop tool transport closed or timed out'}], 'isError': True}
    else:
        response['error'] = {'code': -32601, 'message': 'Method not found'}
    return response


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket', required=True)
    parser.add_argument('--tools-file', required=True, type=Path)
    parser.add_argument('--timeout', type=int, default=900)
    args = parser.parse_args()
    tools = json.loads(args.tools_file.read_text(encoding='utf-8'))
    while True:
        line = sys.stdin.readline(MAX_LINE + 1)
        if not line:
            break
        try:
            if not line.endswith('\n') or len(line.encode('utf-8')) > MAX_LINE:
                raise ValueError('oversized_line')
            response = handle(json.loads(line), tools, lambda name, arguments: tool_call(args.socket, name, arguments, args.timeout))
        except (ValueError, TypeError):
            response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Invalid JSON request'}}
        if response is not None:
            try:
                print(json.dumps(response, ensure_ascii=False), flush=True)
            except BrokenPipeError:
                return


if __name__ == '__main__':
    main()
