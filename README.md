# GLPI Agent

A Python agent that registers a device with a [GLPI](https://glpi-project.org/) server and authenticates it using a unique UUID token.

GLPI is an open-source IT asset and service management tool. Agents like this one run on a device and report to the GLPI server so that the device can be tracked.

## How it works

1. The agent generates a unique **UUID token** (the "agent token").
2. It sends a **registration message** to the GLPI server that includes this token.
3. The server uses the token to **authenticate the device**.
4. After registration, the communication between the agent and the server is **encrypted**.


## Features

- UUID-based agent token for device authentication
- Registration with a GLPI server
- Encrypted communication after registration

## Requirements

- Python 3.[x] or higher
- A running GLPI server (version [x]) reachable from the device
- Python packages listed in `requirements.txt`

## Installation

```bash
git clone https://github.com/tvisha-cmd/GLPI_Agent.git
cd GLPI_Agent
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

Set the server address [in `config.py` / in a `.env` file / as an argument]:

```
GLPI_SERVER_URL=https://your-glpi-server.example.com
```


## Usage

```bash
python [main_file].py
```


## Project structure

```
GLPI_Agent/
├── [main_file].py     # entry point
├── [file].py          # [what it does]
├── requirements.txt
└── README.md
```

## Security notes

- The agent token is a secret. Do not commit real tokens or server addresses to the repository.
- Use HTTPS to reach the GLPI server.

## What I learned

- How device authentication works with a token-based registration
- How to secure client–server communication

## Author

Tvisha Contractor, software engineering student at EPITA
GitHub: [@tvisha-cmd](https://github.com/tvisha-cmd)
