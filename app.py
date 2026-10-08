from flask import Flask, jsonify, request
from flask_cors import CORS

from agent_v2.agent import CustomerSupportAgentV2
from agent_v2.session import SessionManager
