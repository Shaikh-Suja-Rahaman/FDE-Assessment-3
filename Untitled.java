Based on the provided README.md, here is a breakdown of exactly what you need to build for Assessment 3:

The Core Goal You need to build an AI Procurement Request Copilot. This is an internal tool that takes an employee's request to buy software or a service and evaluates it. It should gather facts, check company policies, and then recommend what to do next (e.g., approve, reject, ask for more info), leaving the final decision to a human.

The Deliverables You must implement this system using two different AI architectures:

Architecture A (Single-agent baseline): A basic version using a single AI agent.
Architecture B (Lightweight staged / 2-agent variant): A more complex version that breaks the process down, potentially using two specialized agents or stages.
You will evaluate both using the provided testing harness and write a decision memo on which one you'd actually deploy in the real world.

Where to write your code You need to write your logic in src/solution.py. Specifically, you must implement the function:

python
def handle_request(request_id: str, architecture: str) -> ProcurementDecision:
This function will receive a request_id (e.g., "REQ-123") and an architecture string ("single" or "staged"). It must return an object that matches the ProcurementDecision schema defined in src/contracts.py.

Key Rules & Requirements

Tool Usage: Your agent(s) must use at least 3 tools to gather evidence. At least 1 of these tools must be deterministic (non-AI/code-based). You'll likely need to use tools to read employee data, vendor risk scores (via the provided mock API), and the policy document.
No Auto-Purchasing: The AI can only recommend actions. It cannot actually approve or buy anything.
Handling Uncertainty: If the AI is missing information or finds conflicting data, it shouldn't guess. It should flag the uncertainty and route it to a human.
Pass the Tests: You can test your code against 6 public test cases by running python evals/run_public_evals.py --architecture single and python evals/run_public_evals.py --architecture staged.
Next Steps to Get Started

Look at the Data: Check data/procurement_policy.md to understand the rules your AI needs to enforce, and data/README.md to see what information is available.
Look at the Interfaces: Check src/contracts.py to see exactly what your output needs to look like, and src/data_access.py and src/vendor_client.py for helper functions you can use as tools.
Build Architecture A: Start by writing a simple script in src/solution.py that takes a request, pulls data, sends a prompt to an LLM, and parses the response into a ProcurementDecision.
Would you like me to help you explore the src/contracts.py or the data files to get a better sense of how to start coding src/solution.py?

9:12 PM
now be more specific about what all we have to do. in the sense. of code
9:13 PM
Absolutely. Let's get specific about the Python code you actually have to write.

The only file you are required to modify is 

src/solution.py
.

Right now, it looks like this:

python
from src.contracts import Architecture, ProcurementDecision
def handle_request(request_id: str, architecture: Architecture = "single") -> ProcurementDecision:
    raise NotImplementedError(...)
You need to replace that NotImplementedError with actual logic. Here is a breakdown of the code you have to write to make it work:

1. Set up your AI connection
You'll need to choose an AI provider (like OpenAI, Anthropic, or Google) and install their Python SDK (pip install openai, etc.). At the top of solution.py, you'll initialize your client using the API key you put in .env.

2. Build your "Tools"
The instructions say you need at least 3 tools and at least 1 must be deterministic (a regular Python function, not an AI prompt). You have helper functions ready to be turned into tools in 

src/data_access.py
 and 

src/vendor_client.py
.

You will need to write wrapper functions that your AI can call. For example:

Tool 1: Get Request Details (Deterministic) - Uses data_access.get_request(request_id) to pull the JSON data for what the employee wants to buy.
Tool 2: Check Vendor Risk (Deterministic) - Uses vendor_client.get_vendor_risk(vendor_name) to call the local mock API and get the vendor's security rating.
Tool 3: Search Policy (LLM-based or Deterministic) - Uses data_access.load_policy_text() to read the markdown policy and perhaps uses AI to answer questions about it, or simply returns the text to the main agent.
3. Implement Architecture == "single"
Inside handle_request, if architecture == "single", you need to write code that:

Takes the request_id and passes it to your main LLM agent.
The agent uses its Tools to fetch the request details, check the budget, check the vendor risk, and read the policy.
The agent analyzes the data and outputs a JSON structure.
You parse that JSON into the ProcurementDecision Pydantic model (defined in 

src/contracts.py
) and return it.
4. Implement Architecture == "staged"
If architecture == "staged", you need to write a different workflow. Instead of one massive AI agent doing everything, you break it down in code. For example:

Step 1 (Deterministic Code): You write Python code to fetch the request_id details.
Step 2 (Data Gathering Agent): You pass the request details to an LLM whose only job is to extract the vendor name and cost, then use tools to fetch the risk score and budget limits.
Step 3 (Policy Agent): You pass the gathered facts and the policy text to a second LLM prompt. Its job is to decide the required approvals, identify risk flags, and generate the final ProcurementDecision.
5. Construct the Return Object
Regardless of the architecture, the final lines of your code must construct and return this specific Pydantic object:

python
return ProcurementDecision(
    request_id=request_id,
    recommendation="Approve / Reject / Request More Info",
    evidence=[
        EvidenceItem(source="vendor_api", finding="Vendor SOC2 is expired"),
        EvidenceItem(source="budget_db", finding="Sufficient funds available")
    ],
    required_approvals=["Manager", "Security"],
    missing_information=[],
    risk_flags=["security_review_required"],
    next_step="Route to Security for SOC2 waiver",
    human_review_required=True
)
To summarize your next coding steps:

Pick your AI provider and pip install their SDK.
Open src/solution.py and import the AI SDK, src.data_access, and src.vendor_client.
Write your tool functions.
Write the if architecture == "single": block to prompt the LLM and give it the tools.
Run the tests: python evals/run_public_evals.py --architecture single and see how it performs!
