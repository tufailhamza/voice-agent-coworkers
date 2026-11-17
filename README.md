# AI Neptune Co-working

An intelligent voice agents system for co-working spaces with multiple AI agents to work alongside Humans for different tasks.

| Type | Link |
|------|------|
| **Documentation** | [View Documentation](https://docs.google.com/document/d/1XC_EYuUzcgsnueZgv1bM0HvTqIqE6vu8i69LTxyRrn4/edit?usp=sharing) |
| **Production url** | |
| **Staging url** | |

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd ai-Neptune-coworking
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Setup environment variables**
   ```bash
   cp .env.template .env
   # Edit .env with your configuration
   ```

### Running the Application

#### Local Development Testing

Run `main.py` with agent name in environment for local testing with mode = development:

```bash
# Set environment variables
export AGENT=agent-bella | agent-ryan | agent-transcriber
export MODE=development

# Run the application
python3 main.py console
```
