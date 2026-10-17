# Palantir Foundry Docs — /docs/foundry/workshop/routing

> 来源: https://www.palantir.com/docs/foundry/workshop/routing/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Routing

Workshop routing enables specific states or views of a module to be written to the URL, allowing users to easily share these views with others through link sharing.

![A Workshop module configured with URL routing.](/docs/resources/foundry/workshop/routing-example.png)

## Enable routing

To enable routing for your Workshop module, navigate to the **Pages** section of the **Settings** panel on the left and toggle on the **Enable routing** option.

![Enable routing configuration](/docs/resources/foundry/workshop/enable-routing.png?width=250)

The **Pages** section displays an overview of the pages used with routing and provides an additional panel that showcases the variables in use, as shown below.

![Panel showing overview of variables used in routing](/docs/resources/foundry/workshop/routing-overview.png?width=250)

## Routing for pages

With routing enabled, the ID of the current page will be written to the URL. For pages without a defined page ID, no page ID will be written to the URL; users will be returned to the module's default page on page load.

If your module also uses [variable-backed page selection](/docs/foundry/workshop/variable-backed-layouts/#variable-backed-page-selection), do not set a default value on the page selection variable. The variable's default value takes precedence over the page ID in the URL, so opening a link to a non-default page will briefly render that page and then redirect to the default page.

![Page ID configuration](/docs/resources/foundry/workshop/routing-page-config.png?width=300)

## Routing for variables

You can also use routing to save and share variable values that are configured for use with the module interface. With routing enabled, the current values of variables configured with a routing URL update behavior are written directly as URL query parameters.

To include a module interface variable in the URL, use one of the following configuration options.

- **In URL when used by visible widget or layout:** The URL will only contain the variable's value if the following is true:
  - The value is not the variable's default value
  - The variable is used in a widget or layout that appears in the current view.
- **Always in URL:** The URL will always contain the variable's value if the value is not the variable's default value.
- **Never in URL:** The URL will never contain the variable's value if the routing toggle is disabled.

If a query parameter key matches the external ID of a module interface variable, the value of the query parameter will be used as the variable's initial value, regardless of URL inclusion behavior configured.

![Variable settings options for a Flight Alert object type, featuring routing settings.](/docs/resources/foundry/workshop/variable-settings-for-routing.png?width=400)

## Limitations

### Unsupported variables types in the URL

The following variable types are unable to be used in the URL:

- **Object set filter variables**
- **Object set variables** are limited to single objects, specified by their RID

However, you can define the above variable types indirectly using other routing variables. For instance, you can create a string variable used with routing and define the `default value` of an object set filter variable using this string variable.

### No inheritance of routing configuration from embedded modules

[Embedding a Workshop module](/docs/foundry/workshop/embedding-workshop-modules-overview/) does not carry over the routing configuration of the embedded module. To use variable values from an embedded module with routing, add the desired variables to the child module's [module interface](/docs/foundry/workshop/module-interface/) and pass in routing variables from the parent module in the embedded module configuration.
