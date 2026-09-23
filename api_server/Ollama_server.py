#################################################################
# Author: Sandeep Gupta
# Date: 11/06/2026

import textwrap
import urllib.request
import json
import subprocess
import time

from openai import OpenAI

##################################################################
## COllamaServer: A class to manage the Ollama server and client interactions.
class COllamaServer:
    cURL = "http://localhost:11434/api/tags"

    def __init__(self):
        print("Initialize Ollama server")
        self.mProcess = None

    def check_ollama(self):
        try:
            with urllib.request.urlopen(self.cURL) as response:
                data = json.loads(response.read())
                models = [m["name"] for m in data["models"]]
                print("Ollama is running!")
                print("Available models:", models)
        except Exception as e:
            print("Ollama is NOT running:", e)
            print("Fix: run 'ollama serve' in your terminal")

    def is_running(self):
        """Check if Ollama server is active."""
        try:
            with urllib.request.urlopen(self.cURL) as r:
                return r.status == 200
        except:
            return False

    def start_server(self):
        """Start the Ollama server."""
        if self.is_running():
            print("Ollama is already running.")
            return True

        print("Starting Ollama server...")
        process = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Wait until server is ready
        for _ in range(10):
            time.sleep(1)
            if self.is_running():
                print("Ollama server started!")
                self.mProcess = process
                return True
            print("   waiting...")

        print("Failed to start Ollama server.")
        return False

    def stop_server(self):
        if self.mProcess:
            self.mProcess.terminate()
            self.mProcess.wait()
            print("Server stopped.")
        else:
            print("Not managed by this instance.")

    def get_client(self, aAPI_key="ollama"):
        if not self.is_running():
            print("Ollama server is not running. Please start it first.")
            return
        
        self.mClient = OpenAI(
            base_url="http://localhost:11434/v1",
            api_key=aAPI_key
        )
        print("Ollama client initialized.")
    
    def get_response(self, messages, aModel="gemma3:1b"):
        response = self.mClient.chat.completions.create(
            model=aModel,
            messages=messages
        )

        return response.choices[0].message.content
    
    def build_message(self, aRole, aContent):
        return {"role": aRole, "content": aContent}
    