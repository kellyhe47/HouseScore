Own the Neighborhood - HouseAccount

PRD / The challenge

You get a polygon: ~500 single-family homes in Ramsey, NJ (Bergen County). Nothing else - no address list, no dataset.
Ship a system that produces a Door Score for every home, with evidence, that a field rep could act on tomorrow morning.

Problem & Context
 
Business Context
HouseAccount wins by owning a neighborhood - becoming the default way homeowners find vetted home service providers. We make money when a homeowner engages us across multiple services over years. Our best customers historically: recently moved; busy dual-income households; people who hire out rather than DIY.

Field reps and marketing dollars are finite. Knocking or mailing every door equally is how you lose. Knowing more about every door than anyone else is how you win.

Impact Metrics
Vision pipeline (eval harness — required) - Precision / recall on top signal (vs. labeled samples) - Hallucination rate  - Cost per door ($50 total API budget)      Data engineering   - Territory coverage (% of ~540 homes with data)   - Entity resolution / address match rate   Door Score  - Predictive accuracy vs. hidden ground-truth set (we score their score against outcomes they never see)   Shipped quality   - Deployed + working map UI and MCP server (binary: demo works)

Requirements & Success Criteria

Functional Requirements
1. Agentic data collection
Don't hand-build ETL for sources we name - we're not naming any. Build agents/workflows that discover, harvest, and normalize public data about the territory. 
(Hint: NJ publishes statewide parcel + tax data with polygons, home age, deed dates, sale prices, assessed values - free, via API. There's more out there.) We're evaluating: source discovery, entity resolution across messy address formats, and how much of your pipeline runs without you.

2. Vision pipeline - with evals
Extract signals from imagery (Street View, satellite, listing photos): provider trucks, yard signs, lawn/roof/exterior condition, pools, solar, whatever you can defend as ICP-relevant.
Non-negotiable: ship an eval harness. We hold a hidden labeled test set for this territory. You must present your own:
Labeled samples
Precision/recall on at least your top signal
Hallucination checks
Cost per door

3. The Door Score
Fuse everything into a score you define and defend. Deliverable includes a one-page rationale:
Who is your ideal door? 
Translate the business thesis into a concrete ICP. (How do you detect "dual-income, hires-out" from public data?)
Every signal must trace to that ICP definition
How would you validate this score against real-world outcomes if you had them?

4. Ship it - MCP server + interface
Deployed map UI: every door scored, click for evidence ("truck detected, 2024 imagery" / "deed date 2025-11 - new mover" / "absentee owner")
MCP server exposing your system as tools, e.g. get_door_score(address), explain_score(address), plan_route(hours, start_point) - so an AI assistant can answer: "I've got 2 hours in Ramsey. Which 20 doors, in what order, and what do I say at each one?"
Bonus signal ideas (unrequired - reach is rewarded)
Homeowner layers: absentee-owner detection, tenure, household inference, permit history
Time-series imagery (condition trajectory, provider churn)
Neighborhood effects (provider trucks cluster; competitor density)
Note: owner names are redacted in NJ's bulk data under Daniel's Law. If you find another path to identity data, how you handle it is itself part of the evaluation.

Performance Benchmarks

Weight Dimension
25%
Data engineering - coverage, entity resolution, pipeline autonomy
25%
AI engineering - vision quality measured by evals, cost discipline, agent design
25%
Judgment - ICP reasoning, score defensibility, ethics handling
25%
Shipped quality - deployed, working MCP + map, demo

Code Quality Expectations
  - Working > polished. Evaluation is on shipped, deployed functionality — not lint scores or test coverage percentages.                                                                                                            
  - Reproducible pipeline: a reviewer should be able to re-run your data collection and scoring end-to-end from the repo (README with setup, env vars documented, no hardcoded secrets).                                            
  - Pipeline autonomy is graded: how much runs without manual intervention is an explicit rubric line — brittle hand-run scripts score lower than orchestrated workflows.                                                         
  - Evals are code: the eval harness must be runnable, not a spreadsheet of spot-checks.                                                                                                                                            
  - Defensible in Q&A: you'll be asked "why" about implementation choices — code you can't explain counts against judgment.                                                                                                         
  - Cost discipline in code: caching, batching, and model-tier choices should be visible in the implementation, not just claimed.                                                                                                   
                                                                                                                                  
Technology

Required Languages
None mandated — participant's choice. Python and/or TypeScript recommended (geospatial + agent tooling is strongest there).

AI / ML Frameworks
Any LLM/vision model API (e.g., Claude API, OpenAI API, Gemini); agent frameworks optional (e.g., Claude Agent SDK, LangChain). Must build an MCP (Model Context Protocol) server. Must ship a custom eval harness for the vision pipeline.

Dev Tools
Git required. Map UI framework of choice (e.g., Leaflet, Mapbox, MapLibre). Geospatial tooling as needed (e.g., GeoPandas, Shapely, PostGIS).

Cloud Platforms
Any — but the map UI and MCP server must be deployed and publicly demoable (e.g., Vercel, Netlify, AWS, Fly.io). Local-only submissions fail the "shipped" criterion.    

Other Requirements
- Public data sources only; scraping outside ToS is an automatic judgment failure                                                                                                                                                    
- ~$50 total API budget (Google Street View Static API ≈ $7/1k images — budget accordingly)                                                                                                                                          
- Territory: provided GeoJSON polygon, ~540 single-family homes in Ramsey, NJ                 
- NJ owner names are redacted under Daniel's Law; handling of identity data is part of the evaluation                                                                                                                                
- Deliverables: working map UI, MCP server with tools (get_door_score, explain_score, plan_route), eval results (precision/recall, hallucination rate, cost per door), one-page Door Score rationale  