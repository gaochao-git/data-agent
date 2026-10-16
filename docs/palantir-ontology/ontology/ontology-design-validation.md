# Palantir Foundry Docs — /docs/foundry/ontology/ontology-design-validation

> 来源: https://www.palantir.com/docs/foundry/ontology/ontology-design-validation/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Ontology design: Validation

A structural review confirms that an Ontology is semantically coherent and technically correct. It cannot confirm that anyone can use it. Task-based validation closes that gap: you test the model against the decisions and investigations it must support, with the people and agents who will depend on it.

At Palantir, we use internal analytical drills to test both Ontology design and fluency. Teams receive business questions they have not seen before, ranging from simple counts to multi-step investigations. Participants use general-purpose analytical applications and do not use workflows or dashboards that teams build for the task. This constraint tests the Ontology itself instead of application-specific crutches.

These exercises inform the validation method described here. We recommend adapting these exercises to evaluate Ontology design in your own enterprise. An Ontology is not simply a raw write-through of source systems. It is the interface through which people and agents understand the enterprise, investigate changing conditions, and make decisions.

Five lessons from these exercises shape the guidance that follows:

Lesson| Where it is covered  
---|---  
Structural correctness does not guarantee operational usability| Limits of structural review  
Real business questions are the best acceptance tests| Sourcing real business questions  
New users provide critical evidence| Drill design decisions  
Time to answer is a design signal| Reading the results  
People and agents expose different weaknesses| Comparing people and agents  
  
## Limits of structural review

Structural guidance can determine whether an object type represents a coherent entity, whether a link has meaningful cardinality, or whether multiple types should share an interface. These reviews are essential: they evaluate the internal integrity of the model and prevent design choices that become expensive to reverse. What they cannot evaluate is the experience of approaching the Ontology with only a business question.

That experience does not begin at a schema diagram. It begins with an operational condition: output falls, a shipment arrives late, inventory goes missing, or a delay puts scheduled work at risk. From there the user must translate the condition into the language of the Ontology, find a useful starting object type, and follow links that match how they understand the organization.

At each of those steps, the model either carries the context or transfers that burden back to the user. An ambiguous object type forces a guess. Without an alias, users cannot find an object type by its familiar name. A link that reflects a source-system join rather than a real relationship interrupts the investigation. When two concepts are duplicated, the user must already know which representation is authoritative.

A builder can often navigate these weaknesses from memory, but a builder alone cannot validate the design. The context that makes the path obvious comes from the builder, not the Ontology.

## Sourcing real business questions

The same limitation applies to the questions. If builders inspect the existing Ontology and ask what it can answer, they will validate the coverage they already know they have. This approach systematically excludes the concepts and relationships that never made it into the design.

Instead, derive questions from the operating rhythm of the organization:

  * What do leaders repeatedly ask their teams?
  * What do teams need from one another before they can make a decision?
  * Which investigation depends on an experienced person who knows where every piece of information lives?
  * Which recurring report exists because the underlying answer is otherwise difficult to assemble?
  * What information do users wish they could access without escalating to a technical team?



The strongest questions are both common and consequential, as they reflect decisions that the organization makes repeatedly, under real constraints, and with meaningful costs for delay or error.

Begin at the level where a leader or operator recognizes a significant condition, then move toward the objects and relationships that explain it. A useful sequence generally performs three kinds of work:

  1. **Establish the situation:** Identify the customer, facility, asset, portfolio, or operational result that requires attention.
  2. **Trace the contributing factors:** Follow relationships to the orders, events, materials, positions, or observations behind that result.
  3. **Assess the impact:** Determine what else the situation affects, what it places at risk, and where teams can intervene.



If the Ontology cannot express an important question naturally, keep that question in the set rather than replacing it. Treat the failure itself as a result, and review the design for a missing domain concept, relationship, or piece of business logic. How you run the exercise then determines how much these questions reveal.

## Drill design decisions

Three decisions determine how much a drill reveals: which tools participants may use, who participates, and whether they work alone or in teams.

Purpose-built applications can compensate for weak Ontology design by encoding the correct starting point, path, filters, and calculations in advance. A well-built Workshop application can deliver an excellent workflow and still tell you nothing about whether the underlying model is independently discoverable and expressive. Exclude these applications from the drill.

Who participates determines what the result means. An experienced builder demonstrates what is possible with full knowledge of the implementation. A domain expert who did not build the Ontology provides stronger evidence of whether its names, descriptions, and relationships communicate on their own. A newer user tests whether the model can support onboarding rather than depending on institutional memory.

Team and individual formats reveal different properties. A team exercise tests shared fluency and shows how users explain the model to one another. An individual exercise isolates usability for people who are unfamiliar with the Ontology. When using a team format, begin with an unassisted attempt before allowing coaching so that collective expertise does not hide the original point of confusion.

The drill measures how much work the Ontology performs on the participant's behalf. That makes the observations you record as valuable as the answers you collect.

## Reading the results

Correctness is the minimum. Operational value also depends on how quickly a user can identify the answer, explain the path, and act with confidence.

For each question, capture whether the participant reaches the correct answer and how long the investigation takes. Record where the participant hesitates, which terms they search, which object types and links they try, and whether they need assistance. Manual exports and joins provide particularly important signals: they often indicate that a meaningful relationship exists in the business but not in the Ontology.

The path to the answer can be more informative than the final answer. When a participant becomes confused, ask what they expected to find. Their language can identify a missing alias, an unclear description, or a relationship that is intuitive in the domain but absent from the model. An unexpected path can reveal either a useful alternative or an ambiguity that produces inconsistent results.

Use observed behavior to distinguish symptoms from their most likely causes:

What you observe| What to review  
---|---  
Users cannot identify a starting object type| Unclear names, insufficient aliases, too many visible types, or missing descriptions ([The Misnomer](/docs/foundry/ontology/ontology-anti-patterns/#anti-pattern-the-misnomer))  
Users choose different object types for the same concept| Duplicate, overlapping, or insufficiently differentiated concepts ([Department Silos](/docs/foundry/ontology/ontology-anti-patterns/#anti-pattern-department-silos))  
Users cannot move between concepts they consider related| A missing, misnamed, incorrectly directed, or unavailable link ([Link design](/docs/foundry/ontology/ontology-structural-guidance/#links-and-object-backed-link-types))  
Users export data or construct manual joins| A meaningful relationship or reusable analytical concept that the Ontology does not represent ([Link design](/docs/foundry/ontology/ontology-structural-guidance/#links-and-object-backed-link-types))  
Reasonable paths produce different answers| Unclear levels of detail, such as an order compared with an order line or shipment, or ambiguous identities, units, dates, or aggregation logic ([Naming conventions](/docs/foundry/ontology/ontology-structural-guidance/#naming-conventions))  
Only builders or deeply technical users succeed| Source-system terminology, hidden conventions, or insufficient documentation ([System Silos](/docs/foundry/ontology/ontology-anti-patterns/#anti-pattern-system-silos))  
A reasonable path is consistently slow| Query, indexing, data volume, or modeling performance constraints ([Normalization and derived properties](/docs/foundry/ontology/ontology-structural-guidance/#normalization-and-derived-properties))  
No reasonable path can answer the question| Missing data, a domain coverage gap, or an unsupported analytical operation  
  
Not every difficult task requires an Ontology change. First determine whether the constraint sits in the model, underlying data, permissions, analytical application, or user training. Treat a single failure as a possible edge case, and repeated confusion around the same object type or relationship as evidence of a systemic design problem. To separate a model problem from a knowledge problem, run the same questions again without human participants.

## Comparing people and agents

An Ontology serves two classes of consumers: people and AI agents. Each brings different capabilities to an investigation. People contribute domain experience, organizational context, and implicit knowledge. Agents can search and transform large amounts of data, test several paths, and operate without the same dependence on visual hierarchy.

After completing a drill with people, run the same questions through [AIP Analyst](/docs/foundry/aip-analyst/overview/) or [AI FDE](/docs/foundry/ai-fde/overview/). Provide the question and the necessary Ontology access, but do not provide the path that the human participants take.

The comparison reveals what knowledge the Ontology makes explicit:

Result| What to investigate  
---|---  
People and AI both succeed| Confirm that the model provides a discoverable and expressive path for this question  
AI succeeds, but people struggle| Review human-facing names, aliases, descriptions, default views, and visual hierarchy; also consider whether the task naturally favors automated analysis at scale  
People succeed, but AI struggles| Identify business logic or domain knowledge that participants supplied from experience but that the Ontology does not make explicit  
People and AI both struggle| Look for missing data, links, semantics, or reusable business logic  
  
An AI-only run does not substitute for testing with people. It evaluates a different property: whether the Ontology contains enough context for an agent to reason without relying on the unwritten knowledge of the team. This is the same gap a builder covers from memory, at the scale of the whole organization. If people succeed only because they already know the business, that knowledge remains difficult to automate and difficult to transfer to someone new.

## Exercise formats

We use two complementary exercises. A quiz-style game covers breadth: many independent questions, testing discoverability for people unfamiliar with the Ontology. A timed drill-down covers depth, testing whether teams can sustain one connected investigation from an organization-level question down to granular operational details.

### Quiz-style game

We structure the first exercise as a race between teams. Time pressure forces participants to commit to a path and to make that path visible. Because the questions are unknown in advance, no team can arrive with a workflow already built for one of them. The format is also engaging and interactive.

Use the following guidelines to run the game:

  1. **Keep questions hidden until the game begins.** Teams can prepare by becoming familiar with their Ontology, but they cannot rehearse a specific analysis.
  2. **Assign higher point values to more difficult questions.** Use lower-value questions that require a direct lookup, count, or single-object investigation. Use higher-value questions that require multi-step analysis across several object types and links.
  3. **Designate one participant to control the application.** Ask each team to nominate the participant who is least familiar with the Ontology to operate the application. Other team members can coach, interpret the question, and suggest paths. This structure tests both usability for people unfamiliar with the Ontology and the team's ability to communicate its shared model.
  4. **Use only general-purpose analytical applications.** Permit [Insight](/docs/foundry/insight/overview/), [Contour](/docs/foundry/contour/overview/), and [Quiver](/docs/foundry/quiver/overview/), while excluding purpose-built [Workshop applications](/docs/foundry/workshop/overview/) and AI assistance such as [AIP Analyst](/docs/foundry/aip-analyst/overview/) and [AI FDE](/docs/foundry/ai-fde/overview/). This constraint keeps the Ontology, rather than a prepared interface or agent, at the center of the exercise.
  5. **Require teams to show their work.** Award the points to the first team that produces an answer and demonstrates its analytical path to a judge. Do not accept an unsupported number.



### Timed drill-down

We recommend a second exercise to test depth, continuity, and speed across one connected analytical path. Teams begin with a broad question, then follow a fixed sequence of questions into the underlying operational details.

Use the following guidelines to run the drill-down:

  1. **Build one connected sequence.** Make each question depend on the answer or context established by the question before it.
  2. **Move from the organization-level view to granular details.** Test whether links support a natural investigation across several levels and domains of the Ontology.
  3. **Measure total completion time.** The fastest team wins, but every team's time and analytical path contribute evidence about the design.



Competition is optional in both formats. Its value is the urgency and focus it creates, not the ranking itself.

## Example questions

Each set below follows the same broad-to-granular sequence: establish the situation, trace the contributing factors, assess the impact. Adapt them to the language, decisions, and responsibilities of your organization.

Manufacturing

  1. Which customer ordered the highest product volume this year?
  2. Which product accounts for most of that customer's order volume?
  3. Which work orders produce that product?
  4. Which components do those work orders require (including multiple levels down in the bill of materials)?
  5. Which required components does the build location lack?
  6. Which supplier delay puts the greatest number of scheduled work orders at risk?

Healthcare

  1. Which facility has the highest appointment cancellation rate this quarter?
  2. Which specialty contributes most to that rate?
  3. Which appointment types account for most cancellations?
  4. Which cancellation reasons account for most of those appointments?
  5. Which facilities have capacity to reschedule the affected appointments?
  6. How many upcoming appointments remain at risk of cancellation?

Logistics and distribution

  1. Which region has the highest volume of late deliveries this month?
  2. Which distribution center contributes the most late deliveries in that region?
  3. Which customer orders account for the largest share?
  4. Which shipments fulfill those orders?
  5. Which route segments or handling events contribute most to the delays?
  6. Which upcoming deliveries follow the same routes or pass through the same bottlenecks?

Financial services

  1. Which portfolio has the greatest concentration risk?
  2. Which sector contributes most to that concentration?
  3. Which issuers and positions account for the exposure?
  4. Which recent transactions increased the exposure?
  5. Which linked risk events could affect those positions?
  6. Which proposed transactions would exceed the portfolio's risk limits?

Sensor analysis

  1. Which facility has the largest production shortfall this month?
  2. Which assets contribute most to that shortfall?
  3. Which sensor readings changed before production declined?
  4. Which maintenance events or operating conditions coincide with those changes?
  5. Where has the same sensor pattern occurred before?
  6. Which other assets currently show a similar pattern?

Defense intelligence

  1. How many ships are in a particular area of interest?
  2. Which ship classes account for most of the activity in that area?
  3. Which individual ships entered the area most recently?
  4. Which routes did those ships follow before entering the area?
  5. Which ports did those ships visit along those routes?
  6. Which other ships did they encounter during the same period?

Defense operations

  1. Which unit has the lowest current readiness?
  2. Which subordinate units account for most of the readiness gap?
  3. Which readiness requirements do those units fail to meet?
  4. Which maintenance actions or supply shortages constrain those units?
  5. Which supporting units or locations can provide the required resources?
  6. Which planned exercises conflict with the projected recovery dates?



## Validation as an ongoing practice

A single design cycle will not produce a finished Ontology. Domains evolve, operating conditions change, and new decisions place new demands on the model. Maintain the process instead: repeated testing shows whether the Ontology still represents the organization in a form that people and agents can use.

An initial drill requires only a small set of consequential questions, representative participants who did not build the model, and a general-purpose analytical application. A facilitator records accuracy, time, confusion, and assistance. After the drill, prioritize changes that affect several questions or block high-value workflows. Repeat some questions to measure improvement, then introduce unseen questions to determine whether that improvement generalizes.

Over time, the question set becomes an evaluation suite for the Ontology. Use it to evaluate new object types, links, data sources, and business logic against the decisions the model must support. Lessons from each exercise compound into clearer semantics, more complete organizational context, and faster investigations.

Neither a complete set of desired object types nor an expert who can eventually assemble an answer validates an Ontology. People and agents must repeatedly move from a consequential question to a trustworthy answer through a path that reflects how the organization actually operates.

[âPREVIOUSOntology design: Anti-patterns](/docs/foundry/ontology/ontology-anti-patterns/)

[NEXTOntology search / Semantic search / Overviewâ](/docs/foundry/ontology/overview-semantic-search/)