# Palantir Foundry Docs — /docs/foundry/functions/decorators

> 来源: https://www.palantir.com/docs/foundry/functions/decorators/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Decorators

The following documentation is specific to TypeScript v1 functions. For more [robust capabilities](/docs/foundry/functions/language-feature-support/#typescript-v1-vs-typescript-v2), including support for Ontology SDK and configurable resource requests, we recommend [migrating to TypeScript v2](/docs/foundry/functions/typescript-v2-migration/).

[TypeScript â](https://www.typescriptlang.org/docs/handbook/basic-types.html) functions are declared as methods of a [TypeScript class â](https://www.typescriptlang.org/docs/handbook/classes.html). There are a few requirements for a function to be discovered and published:

  * The method must be `public`
  * The class the method belongs to must be exported from the `functions-typescript/src/index.ts` file
  * The method must be decorated with one of the following decorators imported from the `@foundry/functions-api` package: 
    * `@Function()` for generic functions.
    * [`@OntologyEditFunction()`](/docs/foundry/functions/api-ontology-edits/) for functions that will back an Action. 
      * Object provenance information may be optionally specified with the `@Edits([object type])` decorator when using the [`@OntologyEditFunction()`](/docs/foundry/functions/api-ontology-edits/) method.
      * Object provenance information will be inferred on a best-efforts basis using the static analysis of code if the `@Edits([object type])` decorator is absent.
    * [`@Query({ apiName: "userDefinedAPIName"})`](/docs/foundry/functions/query-functions/) for read-only queries that you want to execute through [Foundry API](/docs/foundry/api/general/overview/introduction/). This decorator should not be used in addition to the `@Function` decorator; it should be used on its own.



Here are examples of functions that are correctly exported in this way:
    
    
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
    14
    15
    16
    17
    18
    19
    20
    import { Function, OntologyEditFunction, Query, Integer, Edits } from "@foundry/functions-api";
    import { Employee, Objects, ObjectSet } from "@foundry/ontology-api";
    
    export class MyUsefulFunctions {
        @Function()
        public incrementNumber(x: Integer): Integer {
            return x + 1;
        }
    
        @Edits(Employee)
        @OntologyEditFunction()
        public updateName(employee: Employee, newName: string): void {
            employee.firstName = newName;
        }
    
        @Query({ apiName: "getEmployeesByName" })
        public async getEmployeesByName(name: string): Promise<ObjectSet<Employee>> {
            return Objects.search().employee().filter(employee => employee.firstName.exactMatch(name));
        }
    }

Any method that is private or not decorated with the relevant decorations will not be published to the function registry. This allows users to create helper functions and utilities for reuse or organization.

Republishing

Each function in a TypeScript repository is uniquely defined by its class name and method nameâif you change the name of the class or method, the function will be published under a new identifier.

[âPREVIOUSGetting started](/docs/foundry/functions/typescript-v1-getting-started/)

[NEXTOntology editsâ](/docs/foundry/functions/api-ontology-edits/)