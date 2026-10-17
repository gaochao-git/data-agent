# Palantir Foundry Docs — /docs/foundry/chatbot-studio/application-state

> 来源: https://www.palantir.com/docs/foundry/chatbot-studio/application-state/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Application state

You can configure multiple string or object set application variables on an AIP Chatbot to configure the **application state**, previously called *parameters*. When an AIP Chatbot with application variables is embedded in the [AIP Chatbot widget](/docs/foundry/workshop/widgets-aip-chatbot/) in Workshop, the list of variables will appear. You can then map each application variable to a [Workshop variable](/docs/foundry/workshop/concepts-variables/) of the corresponding type to show outputs in other widgets.

## Configure application state in AIP Chatbot Studio

When setting up the application state of an AIP Chatbot, configure the following:

- **Identify application variables:** Identify which object set or string variables the LLM should interact with. These can be new variables that the LLM writes results to, or they might already exist in your workflow, such as a variable representing the user’s current selection.
- **Name and describe the variable:** For each variable, write a description that explains its role. This description will be injected into the chatbot's prompt, providing the LLM with context on when to use a given variable.
- **Set value visibility:** You can choose whether chatbots can see values by setting value visibility. We recommend only allowing the LLM to read values when necessary to decrease confusion. For example, if you use an object set variable as an input for [Ontology context](/docs/foundry/chatbot-studio/retrieval-context/#ontology-context), the variable does not need to be visible to the LLM; the relevant contents of the object set value will be in the system prompt in each loop. However, if you have a string variable that contains a dynamic part of your system prompt, the variable *must* be set to visible so the LLM can read the contents of the variable.
- **Add variables as inputs:** You can choose to configure variables as inputs for Ontology retrieval context, allowing a deterministic input for a semantic search or a full object set given to the LLM. Additionally, function-backed context can receive a number of input variables to allow for stateful information to be included when running the function. The [**Object query** tool](/docs/foundry/chatbot-studio/tools/#types-of-tools) can also take in an initial object set variable per object type to provide a starting point for the LLM to apply additional filters or aggregations.

![An example of what the application state configuration panel might look like.](/docs/resources/foundry/chatbot-studio/application-variable-example-agent-studio.png)

Application state can also be referenced in the user-defined **System prompt** by using the slash command `/` on your keyboard.

![An example of an application variable referenced in a prompt.](/docs/resources/foundry/chatbot-studio/application-variable-in-prompt.png)

Application state can be tested using the **Debug application state** section. You can manually override the values of each variable, and the debug section will provide visual feedback when the chatbot updates a variable value.

![An example of what the debug application state section might look like.](/docs/resources/foundry/chatbot-studio/application-variable-debugging-section.png)

## Update application variables with chatbots

The application variables you configure can be modified through chatbot [tools](/docs/foundry/chatbot-studio/tools/) or the [retrieval context](/docs/foundry/chatbot-studio/retrieval-context/) added to the application state configuration. Variables can either be updated deterministically from the output of a tool or context or non-deterministically through the **Update application variable** tool.

### Automatic variable updates

Variables can be configured to deterministically update with values output from the **Object query** tool, function-backed context, or Ontology context. After each execution of the context or tool, Chatbot Studio will record the latest output and update the mapped application variable to the output value when the LLM finishes streaming. We recommend applying deterministic updates rather than non-deterministic updates to avoid LLM confusion. In some cases, you may want to completely hide the variable value from the LLM.

![Configure an AIP Chatbot's output variable for the object query tool.](/docs/resources/foundry/chatbot-studio/object-query-output-variable-example.png)

### Deterministic tool inputs

The **Action** and **Function** [tools](/docs/foundry/chatbot-studio/tools/) can be configured to use inputs from application variables instead of generating inputs dynamically through the LLM. This feature allows you to pass predetermined values directly to tools, improving consistency and reducing token usage.

This feature only supports string and object set input types. The values passed are pinned to the initial value of the variables at the start of the reasoning loop, meaning that updates from previous tool calls in the same query will not be reflected in the values passed as deterministic inputs.

![Configure deterministic tool inputs.](/docs/resources/foundry/chatbot-studio/deterministic-inputs-example.png)

### Update variables with an LLM

If you want an LLM to configure a new variable to update, or conditionally apply an update based on the current user query, add the **Update application variable** tool. This tool supports a list of variables that the LLM can update.

![Configure an AIP Chatbot's Update application variable tool.](/docs/resources/foundry/chatbot-studio/update-application-variable-example.png)

### Variables as citations

You can also configure an object set variable to update when a citation is selected by the user. The configured object set variable will update with a static object set containing the cited object.

![Configure an AIP Chatbot's ontology context citation output variable.](/docs/resources/foundry/chatbot-studio/ontology-context-citation-variable-example.png)

## Configuration in Workshop

If an AIP Chatbot has been configured with an application state, you can configure the chatbot's application variables in Workshop. Review the [AIP Chatbot widget](/docs/foundry/workshop/widgets-aip-chatbot/#configure-the-aip-chatbot) documentation for more information.

![Configure an AIP Chatbot's application variables to Workshop variables in the chatbot's application state section.](/docs/resources/foundry/chatbot-studio/application-variable-in-chatbot-workshop-widget.png)
