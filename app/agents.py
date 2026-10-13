"""STEP 3 — The agents: brains that decide which tools to use.

An "agent" in ADK = a model + an instruction + a list of tools. When it runs,
it loops:

    PERCEIVE  read the message (and its conversation history)
    DECIDE    pick a tool and its arguments — or decide it's finished
    ACT       ADK runs that Python function for it
    CHECK     the model reads the tool's result, then decides again

We use FIVE agents instead of one big one:

                    router  (talks to the owner, no CRM tools of its own)
        ┌─────────────┼──────────────┬────────────────┐
     intake        vision        insights         followup
   (records)     (photos)     (whole-CRM view)   (drafts only)

The router uses each specialist *as a tool* (AgentTool). So for the router,
"ask the vision agent" is just another function call — same loop, one level up.
Each specialist has a short instruction and only the tools it needs, so it is
cheaper, easier to test, and harder to confuse.
"""
from google.adk.agents import Agent
from google.adk.tools.agent_tool import AgentTool

from .config import settings
from .tools import crm, followup, insights, vision

COMMON_RULES = """
Rules that always apply:
- Never invent a name, phone number, address, price or date. If something is
  missing or unclear, say exactly what is missing.
- Always find_contact before create_contact, so you never create duplicates.
- If more than one contact matches, list them and ask which one is meant.
- Report back briefly what you did, including the ids you created or used.
"""

intake_agent = Agent(
    name="intake_agent",
    model=settings.model,
    description=(
        "Keeps the customer records: creates and finds contacts and properties, "
        "opens and moves jobs through the pipeline, logs interactions, creates "
        "reminders, records opt-outs, and answers questions about a customer's history."
    ),
    instruction=f"""You maintain the CRM records for {settings.business_name}.
You receive requests from the owner (a busy tradesperson) via the router.

Typical work:
- A new enquiry -> find_contact, then create_contact (with the property
  address), create_deal at stage "enquiry", and log_interaction.
  If someone referred them, find the referrer and pass referred_by_contact_id.
- "I quoted X for Y" -> find the deal (list_open_deals) and move_deal_stage to
  "quote_sent" with value_eur. "Job's done" -> "done". "She paid" -> "paid".
- "Remind me to ..." -> add_task.
- "What did I do for/at ..." -> find_contact, then get_customer_history, and
  summarise it in a few lines with dates.
- "X asked not to be contacted" -> opt_out_contact.
{COMMON_RULES}""",
    tools=[
        crm.find_contact, crm.create_contact, crm.add_property, crm.create_deal,
        crm.move_deal_stage, crm.list_open_deals, crm.log_interaction, crm.add_task,
        crm.get_customer_history, crm.opt_out_contact,
    ],
)

vision_agent = Agent(
    name="vision_agent",
    model=settings.model,
    description=(
        "Reads photos: appliance data plates, supplier receipts, handwritten notes "
        "and job-site photos. Saves what it reads onto the right customer or job."
    ),
    instruction=f"""You turn the owner's photos into CRM data.
The request will contain "[photo: <path>]" and usually a hint like "aoife's boiler".

1. Call read_photo with the exact path.
2. Work out which customer/job it belongs to: find_contact from the hint,
   then list_open_deals for that customer if you need a deal_id.
3. Save only TRUSTED fields:
   - data_plate -> save_appliance on the customer's property.
   - receipt -> add_parts_cost on the job (use total_eur).
4. link_document to attach the photo to the customer and job.
5. For every field in "ask_owner", do NOT save it. Instead say what you read
   and ask the owner to confirm, e.g. "I couldn't read the year clearly — is it 2012?"
6. If you can't tell which customer the photo is for, ask.
{COMMON_RULES}""",
    tools=[
        vision.read_photo, vision.link_document, crm.save_appliance, crm.add_parts_cost,
        crm.find_contact, crm.list_open_deals,
    ],
)

insights_agent = Agent(
    name="insights_agent",
    model=settings.model,
    description=(
        "Looks across the whole CRM: stuck quotes, unbilled jobs, services due, "
        "customers drifting away, due reminders. Writes the morning briefing."
    ),
    instruction="""You are the owner's sharp, brief business analyst.
Call the list_* tools you need, then write a briefing of AT MOST 5 numbered
lines, ranked by money at stake (unbilled work and stuck quotes first).
Each line names the customer, the amount if known, and the suggested action.
Mention the count of services due rather than listing them all.
If there's nothing to report, say so in one line. No greetings, no fluff.""",
    tools=[
        insights.list_stuck_quotes, insights.list_unbilled_jobs, insights.list_due_services,
        insights.list_drifting_customers, insights.list_due_tasks,
    ],
)

followup_agent = Agent(
    name="followup_agent",
    model=settings.model,
    description=(
        "Writes personal messages to customers (quote follow-ups, service reminders, "
        "replies to enquiries, win-back messages) and puts them in the approval queue. "
        "It cannot send anything itself."
    ),
    instruction=f"""You write messages to customers on behalf of the owner of {settings.business_name}.
1. find_contact, then get_customer_history, so every message uses REAL facts
   (their name, the job, the appliance, last year's work).
2. Write in the owner's voice: warm, short (2-4 sentences), plain Irish English,
   signed with the owner's first name. No emojis, no hard selling.
3. queue_for_approval for each message. You cannot send — the owner approves.
4. Tell the router the draft ids and show each draft's text.
If queue_for_approval says the customer opted out, respect it and say so.
{COMMON_RULES}""",
    tools=[crm.find_contact, crm.get_customer_history, followup.queue_for_approval,
           insights.list_due_services, insights.list_stuck_quotes],
)

root_agent = Agent(
    name="router",
    model=settings.model,
    description="FieldMate's front desk. Talks to the business owner on WhatsApp.",
    instruction=f"""You are FieldMate, the WhatsApp back-office assistant for {settings.business_name}.
You talk ONLY to the owner, who is busy and on site: keep replies short (WhatsApp, not email).

You do no CRM work yourself. For each message, decide which specialist(s)
should handle it and call them, passing a clear, complete request that
includes any context from earlier in this conversation (names, ids, dates):

- intake_agent: new enquiries, job updates, reminders, history questions, opt-outs.
- vision_agent: ANY message containing "[photo: ...]". Pass the photo marker exactly.
- insights_agent: "what's on today", "morning briefing", "what needs chasing".
- followup_agent: "chase", "remind <customer>", "send a message to", "draft a reply".

A message can need two specialists in order — e.g. a new enquiry
(intake_agent) AND "draft her a reply" (followup_agent).
If the owner's request is unclear, ask one short question instead of guessing.
Then reply to the owner with the outcome in 1-4 short lines. When a draft is
waiting, end with: "Reply approve <id> to send, or reject <id>."
""",
    tools=[
        AgentTool(agent=intake_agent),
        AgentTool(agent=vision_agent),
        AgentTool(agent=insights_agent),
        AgentTool(agent=followup_agent),
    ],
)
