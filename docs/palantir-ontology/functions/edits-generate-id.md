# Palantir Foundry Docs — /docs/foundry/functions/edits-generate-id

> 来源: https://www.palantir.com/docs/foundry/functions/edits-generate-id/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Generate unique IDs for new objects

When writing an [Ontology edit function](/docs/foundry/functions/edits-overview/) that creates objects, you may want to generate a unique ID for the newly created object.

## TypeScript v2

TypeScript v2 has no built-in or platform-specific UUID generator. Use a standard UUID library such as the [`uuid` package â](https://www.npmjs.com/package/uuid) from npm.

### Install the package

Add the `uuid` package to your repository's dependencies:
    
    
    Copied!
    
    1
    2
    npm install uuid
    npm install --save-dev @types/uuid

### Use the package in code
    
    
    Copied!
    
    1
    2
    3
    4
    5
    6
    import { v4 as uuidv4 } from 'uuid';
    
    export default function createFlightScenario(): void {
        const uniqueId = uuidv4();
        // Use uniqueId as the primary key for your new object
    }

## TypeScript v1

The following documentation is specific to TypeScript v1 functions. For more [robust capabilities](/docs/foundry/functions/language-feature-support/#typescript-v1-vs-typescript-v2), including support for Ontology SDK and configurable resource requests, we recommend [migrating to TypeScript v2](/docs/foundry/functions/typescript-v2-migration/).

In TypeScript v1 functions, use the `@foundry/functions-utils` package to generate a globally unique identifier.

### Import the package

The `@foundry/functions-utils` package is installed by default, but if the package is not present in the `package.json` file:

  * In the `"dependencies"` section, add `"@foundry/functions-utils": "0.1.0"`



As mentioned in the [documentation on adding dependencies](/docs/foundry/functions/add-dependencies/), remember to restart Code Assist to have the new package available for autocomplete.

### Use the package in code

To generate a unique ID, you can use the `Uuid.random()` utility function from the `@foundry/functions-utils` package. The below code example shows how you could use the `random` function in an example Ontology edit function.
    
    
    Copied!
    
    1
    2
    3
    4
    5
    6
    7
    8
    9
    10
    11
    12
    13
    import { OntologyEditFunction, Timestamp } from "@foundry/functions-api";
    import { Objects } from "@foundry/ontology-api";
    import { Uuid } from "@foundry/functions-utils";
    
    export class ExampleEditFunctions {
        @Edits(FlightScenario)
        @OntologyEditFunction()
        public createFlightScenario(): void {
            const scenario = Objects.create().flightScenarios(Uuid.random());
            scenario.scenarioName = "New scenario";
            scenario.creationTime = Timestamp.now();
        }
    }

[âPREVIOUSOntology edits](/docs/foundry/functions/api-ontology-edits/)

[NEXTError typesâ](/docs/foundry/functions/typescript-error-types/)