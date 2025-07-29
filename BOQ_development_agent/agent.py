from google.adk.agents import LoopAgent, SequentialAgent, ParallelAgent
from .subagent.component_geometry_agent.agent import component_geometry_agent
from .subagent.reinforcement_details_agent.agent import reinforcement_details_agent
from .subagent.material_specs_agent.agent import material_specs_agent
from .subagent.seismic_arrestors_agent.agent import seismic_arrestors_agent
from .subagent.pile_details_agent.agent import pile_details_agent
from .subagent.structural_notes_agent.agent import structural_notes_agent
from .subagent.compliance_parameters_agent.agent import compliance_parameters_agent
from google.adk.agents import Agent
from pydantic import BaseModel, Field
from typing import List

class BoQItem(BaseModel):
    component: str = Field(description="Name or type of the structural component (e.g., Pier P1, Deck Slab)")
    material: str = Field(description="Material description (e.g., Concrete M35, Fe500D TMT Bars)")
    quantity: str = Field(description="Measured quantity with units (e.g., 850 kg, 18.2 m³)")
    specification: str = Field(description="Applicable codes, reinforcement spacing, or other notes")
    category: str = Field(description="BoQ category (e.g., Substructure, Reinforcement, Superstructure)")


class BoQOutput(BaseModel):
    boq: List[BoQItem]


boq_generation_agent = Agent(
    name="BoqGenerationAgent",
    model="gemini-2.5-pro",
    description="Generates a structured Bill of Quantities (BoQ) using outputs from multiple agents, including geometry, reinforcement, material specs, and compliance codes.",
    instruction="""
You will receive JSON inputs from seven structured extraction agents:
- component_geometry
- pile_details
- reinforcement_details
- material_specs
- seismic_arrestors
- structural_notes
- compliance_parameters

Each agent returns structured data about different aspects of a bridge construction project extracted from a PDF. Your task is to intelligently combine and transform these inputs into a unified and organized Bill of Quantities (BoQ).

For each relevant item, output:
- `component`: Name/type of the element (e.g., Pier P1, Deck Slab)
- `material`: Material used (e.g., Concrete M35, Fe500D TMT Bars)
- `quantity`: Value + unit (e.g., 850 kg, 18.2 m³)
- `specification`: Related codes/specs (e.g., IS 456, bar spacing, concrete cover)
- `category`: Logical grouping (e.g., Superstructure, Substructure, Reinforcement)

Use the following logic:
- Use data from `component_geometry`, `pile_details`, and `reinforcement_details` to identify measurable quantities.
- Use `material_specs` and `compliance_parameters` to extract and cross-check material grades and applicable codes.
- Use `structural_notes` to support assumptions, load considerations, or categorization.
- Use `seismic_arrestors` where they appear as physical or regulatory components.
- Group items logically and keep similar categories together.

Return the BoQ in this format:
{
  "boq": [
    {
      "component": "Pier P1",
      "material": "Concrete M35",
      "quantity": "18.2 m³",
      "specification": "IS 456, 50mm cover",
      "category": "Substructure"
    },
    {
      "component": "Deck Slab",
      "material": "Fe500D TMT Bars",
      "quantity": "850 kg",
      "specification": "Top 4T16 @150mm, Bottom 2T12 @200mm",
      "category": "Reinforcement"
    }
  ]
}

Notes:
- Omit empty or irrelevant entries.
- If a field is unavailable, mark its value as "incomplete".
- Ensure units are consistent (e.g., kg, m³, m²).
- Prepare this BoQ as if it will be directly exported to PDF.
""",
    output_schema=BoQOutput,
    output_key="boq"
)


boq_validation_agent = Agent(
    name="BoqValidationAgent",
    model="gemini-2.5-pro",
    description="Validates the generated Bill of Quantities (BoQ) by checking completeness, consistency, unit accuracy, and code compliance.",
    instruction="""
    You will receive a structured Bill of Quantities (BoQ) JSON that was generated from seven extraction agents.

    Your task is to validate the following:

    1. Completeness:
    - Are all major sections (component geometry, pile details, reinforcement, material specs, seismic elements, structural notes, and compliance) represented in the BoQ?

    2. Quantity Accuracy:
    - Are units provided for each quantity?
    - Are numeric values meaningful (not zero, empty, or marked as "incomplete")?

    3. Specification Coverage:
    - Do items mention specifications (e.g., bar spacing, concrete grade)?
    - Are relevant codes included (e.g., IS 456, IS 2911, IRC 83)?

    4. Category Distribution:
    - Are items grouped under logical categories like Superstructure, Substructure, Reinforcement, etc.?

    5. Format and Syntax:
    - Is the JSON response well-formed?
    - Are fields like `component`, `material`, `quantity`, `specification`, and `category` present for each item?

    Also ensure:
    - If the term `"incomplete"` appears in any field, flag it.
    - If any required sections (e.g., Reinforcement, Piles) are missing or underpopulated, include that in the `issues`.
    - Units should be standardized (kg, m³, m², etc.) and match the type of material.

    Return one of the following responses:

    If BoQ is valid:
    {
    "validation": "pass",
    "issues": []
    }

    If BoQ is invalid:
    {
    "validation": "fail",
    "issues": [
        "Missing reinforcement quantity for Pier P1",
        "Incomplete material specification for Deck Slab",
        "Category 'Substructure' is missing",
        "Quantity format invalid: '850'"
    ]
    }

    Be specific in your feedback so the BoQ can be regenerated correctly if needed.
    """
    ,
    output_key="validation",
)

# Create the Sequential Pipeline
# category_extraction_agent = SequentialAgent(
#     name="CategoryExtractionAgent",
#     sub_agents=[
#         component_geometry_agent,
#         pile_details_agent,
#         reinforcement_details_agent,
#         material_specs_agent,
#         seismic_arrestors_agent,
#         structural_notes_agent,
#         compliance_parameters_agent,
#     ]
# )
category_extraction_agent = ParallelAgent(
     name="ParallelWebResearchAgent",
     sub_agents=[
        component_geometry_agent,
        pile_details_agent,
        reinforcement_details_agent,
        material_specs_agent,
        seismic_arrestors_agent,
        structural_notes_agent,
        compliance_parameters_agent
        ],
     description="Runs multiple research agents in parallel to gather information."
 )



boq_loop_agent = LoopAgent(
    name="BoqLoopAgent",
    description="This agent coordinates the iterative process of generating and validating the Bill of Quantities (BoQ). It loops between BoQ generation and validation agents up to a maximum of 3 attempts. If validation fails, it regenerates the BoQ using updated structured data from previous extraction agents until a valid, standards-compliant BoQ is achieved.",
    max_iterations=3,  
    sub_agents=[
        boq_generation_agent,
        boq_validation_agent,
    ]
)



root_agent = SequentialAgent(
    name="RootAgent",
    description="This agent orchestrates the full construction document analysis pipeline. It first triggers structured information extraction from multiple specialized agents (geometry, materials, reinforcement, etc.), then enters a loop to generate and validate a Bill of Quantities (BoQ). Upon successful validation, the BoQ is finalized and can be exported.",
    sub_agents=[
        category_extraction_agent,
        boq_loop_agent
    ]
)