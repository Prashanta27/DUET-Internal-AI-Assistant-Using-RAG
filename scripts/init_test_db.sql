-- Runs once, on first cluster init. Gives pytest a real database that is
-- separate from the development one.
CREATE DATABASE duet_test OWNER duet;
