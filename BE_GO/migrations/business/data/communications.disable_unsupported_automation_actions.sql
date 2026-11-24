WITH supported AS (
    SELECT w.automation_workflow_id, jsonb_agg(node ORDER BY position) FILTER (
        WHERE NOT coalesce(jsonb_typeof(node) = 'object' AND node->>'id' = 'message', false)
           OR node->'value' = '"email"'::jsonb
    ) AS tree
    FROM communications_automationworkflow w
    CROSS JOIN LATERAL jsonb_array_elements(CASE jsonb_typeof(w.automation_tree) WHEN 'array' THEN w.automation_tree ELSE '[]'::jsonb END) WITH ORDINALITY AS nodes(node, position)
    WHERE jsonb_typeof(w.automation_tree) = 'array'
    GROUP BY w.automation_workflow_id
)
UPDATE communications_automationworkflow w
SET automation_tree = coalesce(supported.tree, '[]'::jsonb), enabled = false
FROM supported
WHERE w.automation_workflow_id = supported.automation_workflow_id
  AND w.automation_tree IS DISTINCT FROM coalesce(supported.tree, '[]'::jsonb);
