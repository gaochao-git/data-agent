# Palantir Foundry Docs — /docs/foundry/functions/create-custom-aggregation

> 来源: https://www.palantir.com/docs/foundry/functions/create-custom-aggregation/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Create a custom aggregation

Functions can be used to compute custom aggregations based on data in the ontology, which can then be surfaced in a chart widget in Workshop. This guide walks through how to write custom aggregation logic that loads aggregated data from the ontology, manipulates the results to create a projection of future results, and returns the modified results.

This page covers TypeScript v1. For Python, see [Create a custom aggregation with Python functions](/docs/foundry/functions/python-functions-create-custom-aggregation/); for TypeScript v2, see [Aggregations](/docs/foundry/ontology-sdk/typescript-osdk/#aggregations) in the OSDK reference.

These references may be useful while working through this section:

  * Reference for [Object Set aggregations](/docs/foundry/functions/api-object-sets/#computing-aggregations)
  * Reference for [aggregation types](/docs/foundry/functions/types-reference/#aggregation-types)
  * [Create a custom aggregation with Python functions](/docs/foundry/functions/python-functions-create-custom-aggregation/), the Python version of this guide
  * [Aggregations](/docs/foundry/ontology-sdk/typescript-osdk/#aggregations) in the TypeScript OSDK reference, for the TypeScript v2 aggregation vocabulary



## Loading an aggregation

TypeScript v1 builds an aggregation as a chain of `.groupBy()`, `.segmentBy()`, and a metric method on `Objects.search()`. A TypeScript v1 aggregation metric only accepts properties marked as searchable. A string property offers two bucketing strategies, each bounded by a maximum bucket count and approximate above it: `.topValues()` buckets by the top 1,000 values and answers quickly, and `.exactValues()` considers up to 10,000 buckets and is correspondingly slower.

For the TypeScript v2 aggregation vocabulary, see [Aggregations](/docs/foundry/ontology-sdk/typescript-osdk/#aggregations) in the TypeScript OSDK reference. The migration guide maps every TypeScript v1 [bucketing method](/docs/foundry/functions/typescript-v2-migration/#group-by-strategy-mapping) and [aggregation metric](/docs/foundry/functions/typescript-v2-migration/#aggregation-metric-mapping) to its TypeScript v2 equivalent, and lists the [capabilities with no TypeScript v2 equivalent](/docs/foundry/functions/typescript-v2-migration/#capabilities-with-no-typescript-v2-equivalent).

In this example, assume you have an ontology consisting of expenses, with each `expense` object having properties for department name, expense `date`, and expense `amount`. If you want to estimate the monthly spend by department over the next six months, you can begin by loading the aggregated data for the monthly spend:

TypeScript v1
    
    
    Copied!
    
    1
    2
    3
    4
    5
    const result = await Objects.search()
        .expenses()
        .groupBy(expense => expense.departmentName.topValues())
        .segmentBy(expense => expense.date.byMonth())
        .sum(expense => expense.amount);

## Manipulating aggregation results

Calling `.segmentBy()` after `.groupBy()` produces a `ThreeDimensionalAggregation`, so `result.buckets` is already nested one bucket per department. Each inner bucket key is an `IRange<Timestamp>` that carries both endpoints of the month, with an inclusive `min` and an exclusive `max`, and each `Timestamp` value supports calendar arithmetic through methods such as `.plusMonths()`.

Next, you can extrapolate the spend for each department for the next six months. For this example, you can take a simple approach of using the final month's value as the estimate for the next six months.

TypeScript v1
    
    
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
    21
    22
    23
    24
    25
    const modifiedBuckets = result.buckets.map(bucket => {
        // Find the bucket corresponding to the most recent month
        const lastBucket = bucket.value[bucket.value.length - 1];
    
        let nextSixMonths: IBaseBucket<IRange<Timestamp>, Double>[] = [];
        let currentMonth = lastBucket.key.max!;
        // Loop six times
        for (let i = 0; i < 6; i++) {
            // Find the end of this range (the following month)
            const nextMonth = currentMonth.plusMonths(1);
            // Add a new bucket which uses the next month as the date range
            // and the most recent month as the value
            nextSixMonths.push({
                key: {
                    min: currentMonth,
                    max: nextMonth,
                },
                value: lastBucket.value,
            });
            currentMonth = nextMonth;
        }
    
        // Return the modified results
        return { key: bucket.key, value: nextSixMonths };
    });

In TypeScript v2, `.aggregate()` returns a flat array of rows rather than nested buckets, so a function that must return a three-dimensional aggregation must rebuild the nesting itself. Review [Grouping](/docs/foundry/functions/typescript-v2-migration/#grouping) in the migration guide.

## Returning the aggregation

Now that you have created an estimate for the next six months, you can return these estimated values:

TypeScript v1
    
    
    Copied!
    
    1
    return { buckets: modifiedBuckets };

TypeScript v1 wraps the buckets in an object under a `buckets` key, with the nested level keyed `value`. The return type stays `ThreeDimensionalAggregation` across TypeScript v1, TypeScript v2, and Python, and each version builds that value differently inside the function, so a Workshop chart that consumes this function keeps working after a migration. Review [Aggregation types](/docs/foundry/functions/types-reference/#aggregation-types) for the shape each version expects, and [Create a custom aggregation with Python functions](/docs/foundry/functions/python-functions-create-custom-aggregation/) for the Python `NestedBucket` and `SingleBucket` classes.

The full example code for this function is as follows:

TypeScript v1
    
    
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
    21
    22
    23
    24
    25
    26
    27
    28
    29
    30
    31
    32
    33
    34
    35
    36
    @Function()
    public async estimatedDepartmentExpenses(): Promise<ThreeDimensionalAggregation<string, IRange<Timestamp>, Double>> {
        const result = await Objects.search()
            .expenses()
            .groupBy(expense => expense.departmentName.topValues())
            .segmentBy(expense => expense.date.byMonth())
            .sum(expense => expense.amount);
    
        const modifiedBuckets = result.buckets.map(bucket => {
            // Find the bucket corresponding to the most recent month
            const lastBucket = bucket.value[bucket.value.length - 1];
    
            let nextSixMonths: IBaseBucket<IRange<Timestamp>, Double>[] = [];
            let currentMonth = lastBucket.key.max!;
            // Loop six times
            for (let i = 0; i < 6; i++) {
                // Find the end of this range (the following month)
                const nextMonth = currentMonth.plusMonths(1);
                // Add a new bucket which uses the next month as the date range
                // and the most recent month as the value
                nextSixMonths.push({
                    key: {
                        min: currentMonth,
                        max: nextMonth,
                    },
                    value: lastBucket.value,
                });
                currentMonth = nextMonth;
            }
    
            // Return the modified results
            return { key: bucket.key, value: nextSixMonths };
        });
    
        return { buckets: modifiedBuckets };
    }

The resulting aggregation can be used in a Workshop chart to show the monthly spend estimate for the next six months.

[âPREVIOUSObject identifiers](/docs/foundry/functions/object-identifiers/)

[NEXTImport object, interface, and link typesâ](/docs/foundry/functions/ontology-imports/)