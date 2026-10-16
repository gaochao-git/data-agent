# Palantir Foundry Docs — /docs/foundry/functions/undefined-values

> 来源: https://www.palantir.com/docs/foundry/functions/undefined-values/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Handle undefined values

The following documentation is specific to TypeScript v1 functions. For more [robust capabilities](/docs/foundry/functions/language-feature-support/#typescript-v1-vs-typescript-v2), including support for Ontology SDK and configurable resource requests, we recommend [migrating to TypeScript v2](/docs/foundry/functions/typescript-v2-migration/).

Below are two useful patterns for handling `undefined` values which may be returned from accessing properties or links.

### Explicit checks
    
    
    Copied!
    
    1
    2
    3
    4
    5
    6
    7
    @Function()
    public getFullName(employee: Employee): string {
        if (!(employee.firstName && employee.lastName)) {
            throw new UserFacingError("Cannot derive full name because either first or last name is undefined.");
        }
        return employee.firstName + " " + employee.lastName;
    }

By checking that both the `firstName` and `lastName` fields are defined, the TypeScript compiler knows that the final line with the `return` statement can compile correctly. The benefit of this approach is that type checking is more explicit, and in the case where `undefined` values are present, you can throw a more explicit error about what went wrong.

### Non-null assertion operator

You can use the TypeScript [non-null assertion operator â](https://www.typescriptlang.org/docs/handbook/release-notes/typescript-2-0.html#non-null-assertion-operator) (`!`) to ignore the `undefined` case.
    
    
    Copied!
    
    1
    2
    3
    4
    @Function()
    public getFullName(employee: Employee): string {
        return employee.firstName! + " " + employee.lastName!;
    }

This approach simply overrides the TypeScript compiler and asserts that the fields you are accessing are defined. Although this makes for more concise code, this can lead to cryptic errors in the case when one of the fields turns out to be `undefined`. We recommend making explicit checks when possible.

[âPREVIOUSError types](/docs/foundry/functions/typescript-error-types/)

[NEXTDebug functionsâ](/docs/foundry/functions/debug/)