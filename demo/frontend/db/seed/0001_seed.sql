-- Demo restaurants and tables (fictional). Factory rows contain scope only; no evidence is seeded.
INSERT INTO restaurants (slug, name, cuisine, city, timezone, opens_at, closes_at, slot_minutes, description) VALUES
('ember-oak','Ember & Oak','Wood-fire Nordic','Stockholm','Europe/Stockholm','17:00','23:00',90,'Open-flame cooking with seasonal Swedish produce.'),
('canal-no-9','Canal No. 9','Modern bistro','London','Europe/London','12:00','22:30',90,'Small plates by the water.'),
('hudson-lantern','Hudson Lantern','New American','New York','America/New_York','17:30','23:30',90,'Late-night counter with a raw bar.'),
('kiri-ya','Kiri-ya','Kaiseki','Tokyo','Asia/Tokyo','18:00','22:00',120,'Eight seats, one seasonal tasting menu.')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO dining_tables (restaurant_id, label, seats)
SELECT r.id, x.label, x.seats FROM restaurants r
CROSS JOIN (VALUES ('T1',2),('T2',2),('T3',4),('T4',4),('T5',6)) AS x(label, seats)
WHERE r.slug <> 'kiri-ya'
ON CONFLICT DO NOTHING;
INSERT INTO dining_tables (restaurant_id, label, seats)
SELECT r.id, x.label, x.seats FROM restaurants r
CROSS JOIN (VALUES ('Counter A',4),('Counter B',4)) AS x(label, seats) WHERE r.slug = 'kiri-ya'
ON CONFLICT DO NOTHING;

INSERT INTO factory_stages (number, title, status, summary) VALUES
(1,'Stage 1','pending','Not yet verified.'),
(2,'Stage 2','pending','Not yet verified.'),
(3,'Stage 3','pending','Not yet verified.'),
(4,'Stage 4','pending','Not yet verified.')
ON CONFLICT (number) DO NOTHING;

-- Seat roles and generic mandates are a proposed design. Agent identities (model) are pending.
INSERT INTO seats (name, role, model, mandate, sort) VALUES
('Foreman','Planner','','Break the brief into small, verifiable tasks. Define acceptance checks before work starts. Never write production code.',1),
('Builder','Implementer','','Take one task at a time. Make the smallest change that satisfies its checks. Attach evidence of what changed and why.',2),
('Inspector','Reviewer','','Read every change against its task. Reject work that lacks evidence or widens scope. Explain each rejection in one sentence.',3),
('Auditor','Verifier','','Run the checks independently of the builder. Try to break the result with adversarial inputs. Report pass or fail with reproducible proof.',4);
