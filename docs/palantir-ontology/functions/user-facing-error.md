# Palantir Foundry Docs — /docs/foundry/functions/user-facing-error

> 来源: https://www.palantir.com/docs/foundry/functions/user-facing-error/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# User-facing errors

When running functions in other parts of the platform, such as Workshop or actions, you may want to throw an error with a detailed message. To do so, throw a `UserFacingError`. For example:

TypeScript v1TypeScript v2Python
    
    
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
    import { Function, UserFacingError } from "@foundry/functions-api";
    import { Employee } from "@foundry/ontology-api";
    
    export class MyFunctions {
        @Function()
        public async searchExactlyFiveEmployees(employees: Employee[]): Proimse<string> {
            if (employees.length != 5) {
                throw new UserFacingError(`Pass in exactly 5 employees. Received ${employees.length}.`);
            }
    
            // search employees
        }
    }
    
    
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
    import { Osdk } from "@osdk/client";
    import { Employee } from "@ontology/sdk";
    import { UserFacingError } from "@osdk/functions";
    
    export default async function searchExactlyFiveEmployees(employees: Array<Osdk.Instance<Employee>>): Promise<string> {
        if (employees.length != 5) {
            throw new UserFacingError(`Pass in exactly 5 employees. Received ${employees.length}.`);
        }
    
        // search employees
    }
    
    
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
    from functions.api import function, UserFacingError
    from ontology_sdk import FoundryClient
    from ontology_sdk.ontology.objects import Aircraft
    
    @function()
    def search_exactly_five_employees(
        employees: list[Aircraft]
    ) -> str:
        if not len(aircraft) == 5:
            raise UserFacingError(f"Pass in exactly 5 employees. Received ${len(aircraft)}.")
    
        # search employees

When running this as a [Function-backed Action](/docs/foundry/action-types/function-actions-overview/) in a [Workshop application](/docs/foundry/workshop/functions-use/) with an incorrect number of employees, users will see the following error:

When a function used in a [function-backed export](/docs/foundry/workshop/widgets-button-group/#function-backed-export) throws a `UserFacingError`, the export failure toast displays the error message.

A detailed user-facing error message helps users identify and resolve the issue.

[âPREVIOUSDeploy functions](/docs/foundry/functions/functions-deployed/)

[NEXTStreaming functionsâ](/docs/foundry/functions/streaming-functions/)