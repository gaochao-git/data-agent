# Palantir Foundry Docs — /docs/foundry/developer-console/create-application

> 来源: https://www.palantir.com/docs/foundry/developer-console/create-application
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Create a new Developer Console application

On this page, we will walk through the following processes of creating a new Developer Console application:

- Adding object types, Action types, and other Ontology resources to the SDK application.
- Adding operations and resources for Platform API access.
- Generating packages in any of the supported languages.
- Using custom documentation generated for your application-specific SDK.

## Create an application using Developer Console

[Navigate to Developer Console](/docs/foundry/developer-console/overview/) in your Foundry instance, then select **+ New application**.

If you do not see the **+ New application** button, you may require additional permissions. See the [permissions documentation](/docs/foundry/developer-console/permissions/#user-permissions) for more details.

Next, follow the steps in the creation wizard that appears and add the following details:

- On the **Basic information** page, add an icon to your application; the icon will be used to identify the application when the user is presented with a consent screen.

![The OAuth consent screen preview displays the application icon and name.](/docs/resources/foundry/developer-console/oauth-consent-screen-preview.png)

- On the **Application type** page, choose [**Client-facing application**](/docs/foundry/developer-console/permissions/). For a backend service or server-side application that uses a service user, choose **Backend service** instead and follow [Bootstrap a back-end TypeScript application](/docs/foundry/developer-console/how-to-bootstrapping-server-side-typescript/) for a complete walkthrough.

![The application type selection page with client-facing application selected.](/docs/resources/foundry/developer-console/app-type-client-facing.png)

- On the **Authorization code grant** section of the **Permissions** page, set the redirect URL to `http://localhost:8080/auth/callback`.

![The redirect URL is correctly entered in the Redirect URL field in the Authorization code grant section.](/docs/resources/foundry/developer-console/oauth-redirect-url-config.png)

Follow the instructions in [Configure CORS](/docs/foundry/administration/configure-cors/) to add `http://localhost:8080` to your CORS policy in Control Panel.
If you do not have permission to configure CORS and your Foundry administrator is unable to configure CORS for you, set the redirect URL to `https://localhost:8080/auth/callback`.

### Resources

- On the **Resources** page, select **Yes, generate an Ontology SDK**.

![Select the option to indicate you want to use the Ontology SDK.](/docs/resources/foundry/developer-console/app-use-ontology-sdk-option.png)

- Select an Ontology to use. You can select any Ontology regardless of namespace.

Once the Ontology SDK has been generated, the Ontology associated with it cannot be changed.

- Select the object types and action types that you want the OSDK package to include. For this exercise, pick any object type available to you.

![Select the Ontology you want to use in the SDK and the specific object types or action types.](/docs/resources/foundry/developer-console/app-ontology-resource-scopes.png)

The data entities that you choose control two aspects of your application. Language-specific bindings and the integrated API documentation are generated based on the entities you select. The token you receive as a result of going through the OAuth 2.0 flow is also restricted by default to the set of entities selected. For more details, see [Resource access restrictions](/docs/foundry/developer-console/permissions/#resource-access-restrictions).

- If you need to make [Platform API requests](/docs/foundry/api/general/overview/introduction/) using your application, be sure to add the appropriate resources and operations through the **Platform SDK** tab.

![Use the Platform SDK tab to include Platform SDK resources.](/docs/resources/foundry/developer-console/scopes-platform-sdk-resources.png)

The operations granted in the **Client allowed operations** table may allow applications to access underlying service endpoints.

- Review and confirm the information you entered, then select **Create application** to create the application.
- Finally, you must select **Generate first version** to get the first version of the created package.

![Select the Generate first version button to create your SDK.](/docs/resources/foundry/developer-console/sdk-generate-first.png)

## Set application restrictions after creation

The creation wizard does not include a restricted or unrestricted setting. Every application is created as a [restricted application](/docs/foundry/developer-console/application-restrictions/#restricted-applications), which means Developer Console controls the resources and operations the application may use in Foundry.

If your application instead needs broad, platform-wide access—for example, to reach resources that are not inside a project—change the setting once the application exists:

1. Open the application and select **OAuth & restrictions** from the left navigation panel.
2. Under **Application restrictions**, select **Unrestricted**.

For the full sequence, including the authentication choices to make while creating the application, see [Create an unrestricted application](/docs/foundry/developer-console/application-restrictions/#create-an-unrestricted-application).

## Ontology-specific documentation

The Developer Console generates documentation based on the Ontology entities you selected. This documentation is available for TypeScript, Python, and cURL; you can switch between the different languages using the dropdown menu in the upper right corner of the Console.

![Ontology-specific documentation for the "Aircraft" object type in the Developer Console.](/docs/resources/foundry/developer-console/app-ontology-documentation.png)

In the example above, each object type, action type, and function is documented. The documentation includes code examples of how specific properties are returned or how parameters can be used; you can copy and paste these examples directly into your code.

## Next steps

- **[Set application restrictions](#set-application-restrictions-after-creation):** Every application is created restricted. Change this if your application needs broad, platform-wide access.
- **[Request a scope at authorization time](/docs/foundry/developer-console/application-restrictions/#request-a-scope-at-authorization-time):** Define the operations your application requests in code.
- **[Host your application on Foundry](/docs/foundry/developer-console/deploy-custom-application-on-foundry/):** Serve a frontend-only application from a Foundry subdomain.
