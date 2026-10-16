# Palantir Foundry Docs — /docs/foundry/action-types/function-actions-batched-execution

> 来源: https://www.palantir.com/docs/foundry/action-types/function-actions-batched-execution/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Batched execution

When an action is triggered in batches, such as in [Workshop inline edits](/docs/foundry/workshop/widgets-object-table/#inline-edits-cell-level-writeback) or in [Automate](/docs/foundry/automate/execution-settings/), the backing function is usually called once per request in sequence, and all edits are applied atomically at the end of the action call.

Alternatively, to improve performance or resolve edit conflicts, you may wish to configure a function to receive the whole batch of action calls in a single execution.

To enable batched execution, the function must receive _a single input parameter_ containing _a list of structs_ (also known as a "map" or "dictionary"). You will then be able to enable batched execution and pass data into the fields of this struct in the same way you would usually pass data to a function's top-level inputs.

When using batched execution:

  * A single action call will invoke a single function execution with _a single entry_ in the list input parameter.
  * A batched action call will invoke a single function execution with _several entries_ in the list input parameter.



### Example

Instead of a function-backed action with the following signature:
    
    
    Copied!
    
    1
    2
    3
    4
    @OntologyEditFunction()
      public updateDestination(flight: Flight, destination: Airport): void {
        // update flight object
    }

A function can instead receive a "batch" of requests and process them all in a single execution:
    
    
    Copied!
    
    1
    2
    3
    4
    5
    6
    @OntologyEditFunction()
    public updateDestinationBatch(batch: {flight: Flight, destination: Airport}[]): void {
        batch.forEach(({flight, destination}) => {
          // update flight object
        });
    }

You can then enable batched execution for this function when configuring the action type:

[âPREVIOUSGetting started](/docs/foundry/action-types/function-actions-getting-started/)

[NEXTSide effects / Overviewâ](/docs/foundry/action-types/side-effects-overview/)