"""Demo seed: one organisation, a mission, a director and a few open issues.

Runs only when the database has no company yet. The director is provisioned
on the default runtime straight away, so the first assignment already works.
"""

import db
import org


async def seed_if_empty() -> None:
    if db.one("SELECT id FROM companies LIMIT 1"):
        return
    company = db.insert(
        "companies",
        name="Regiekamer demo",
        mission="Help het ministerie sneller tot onderbouwde beleidskeuzes te komen, met een team van AI-collega's "
        "dat onder regie van mensen werkt.",
        budget_monthly_cents=10000,
        require_board_approval_for_new_agents=0,
        issue_prefix="RK",
        created_at=db.now(),
    )
    goal = db.insert(
        "goals",
        company_id=company["id"],
        title="Binnen een kwartaal drie beleidsvragen end-to-end laten voorbereiden door het AI-team",
        level="company",
        created_at=db.now(),
    )
    ceo = org.hire_agent(company["id"], {"template": "ceo", "name": "Directeur Dewi"})
    await org.activate_agent(ceo["id"])
    # Board approval is on by default for everyone hired after the director.
    db.update("companies", company["id"], require_board_approval_for_new_agents=1)
    db.update("goals", goal["id"], owner_agent_id=ceo["id"])

    for title, description, priority in [
        ("Verken inzet van AI bij het beantwoorden van Kamervragen",
         "Welke stappen in het proces rond Kamervragen lenen zich voor AI-ondersteuning, wat zijn de risico's "
         "(AVG, AI-verordening) en wat is een verstandige eerste pilot?", "high"),
        ("Schrijf een B1-uitleg over de nieuwe toeslagenregeling",
         "Maak een korte, begrijpelijke uitleg voor burgers. Kernboodschap + concepttekst van max. 250 woorden.", "medium"),
        ("Stel indicatoren op voor het meten van regeldruk",
         "Welke indicatoren en databronnen zijn geschikt om regeldruk voor ondernemers te volgen?", "low"),
    ]:
        org.create_issue(company["id"], {"title": title, "description": description, "priority": priority,
                                         "goal_id": goal["id"]})
