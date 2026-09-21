CREATE TABLE feedback_responses (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    respondent_name VARCHAR(255) NOT NULL DEFAULT 'Anonymous',
    rating INT NOT NULL,
    category VARCHAR(100) NOT NULL,
    comments TEXT,
    event_name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
