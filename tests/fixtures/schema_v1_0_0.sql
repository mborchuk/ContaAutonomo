-- Schema created by ContaAutonomo v1.0.0 (docker_entrypoint.init() at tag v1.0.0).
-- DDL only, no data. Used by tests/test_schema_migrations.py.
CREATE TABLE customer (
	id INTEGER NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	vat_number VARCHAR(100), 
	address TEXT, 
	city VARCHAR(100), 
	postal_code VARCHAR(20), 
	country VARCHAR(100), 
	email VARCHAR(200), 
	phone VARCHAR(50), 
	is_default BOOLEAN, 
	tax_type VARCHAR(20), 
	created_at DATETIME, 
	PRIMARY KEY (id)
);
CREATE TABLE bank (
	id INTEGER NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	iban VARCHAR(100) NOT NULL, 
	swift VARCHAR(50), 
	bank_name VARCHAR(200), 
	is_default BOOLEAN, 
	created_at DATETIME, 
	PRIMARY KEY (id)
);
CREATE TABLE settings (
	id INTEGER NOT NULL, 
	business_name VARCHAR(200), 
	owner_name VARCHAR(200), 
	vat_number VARCHAR(100), 
	nie_number VARCHAR(100), 
	address TEXT, 
	city VARCHAR(100), 
	postal_code VARCHAR(20), 
	country VARCHAR(100), 
	phone VARCHAR(50), 
	email VARCHAR(200), 
	default_payment_terms VARCHAR(200), 
	default_description TEXT, 
	default_notes TEXT, 
	default_currency VARCHAR(10), 
	tracked_currencies TEXT, 
	base_currency VARCHAR(10), 
	report_template VARCHAR(100), 
	invoice_template VARCHAR(100), 
	show_currency_panel BOOLEAN, 
	show_tax_panel BOOLEAN, 
	auto_backup_enabled BOOLEAN, 
	backup_retention_count INTEGER, 
	daily_backup_retention_count INTEGER, 
	social_security_monthly FLOAT, 
	log_path VARCHAR(500), 
	log_retention_days INTEGER, 
	log_use_external_storage BOOLEAN, 
	log_storage VARCHAR(10), 
	default_vat_rate FLOAT, 
	default_irpf_rate FLOAT, 
	updated_at DATETIME, 
	PRIMARY KEY (id)
);
CREATE TABLE contractor (
	id INTEGER NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	vat_number VARCHAR(100), 
	address TEXT, 
	city VARCHAR(100), 
	postal_code VARCHAR(20), 
	country VARCHAR(100), 
	email VARCHAR(200), 
	phone VARCHAR(50), 
	notes TEXT, 
	created_at DATETIME, 
	PRIMARY KEY (id)
);
CREATE TABLE tax_form (
	id INTEGER NOT NULL, 
	form_type VARCHAR(50) NOT NULL, 
	year INTEGER NOT NULL, 
	quarter INTEGER, 
	file_path VARCHAR(500) NOT NULL, 
	original_filename VARCHAR(200), 
	uploaded_at DATETIME, 
	notes TEXT, 
	PRIMARY KEY (id)
);
CREATE TABLE document (
	id INTEGER NOT NULL, 
	name VARCHAR(300) NOT NULL, 
	source VARCHAR(100), 
	document_date DATE, 
	description TEXT, 
	file_path VARCHAR(500) NOT NULL, 
	original_filename VARCHAR(300), 
	created_at DATETIME, 
	PRIMARY KEY (id)
);
CREATE TABLE ss_payment (
	id INTEGER NOT NULL, 
	payment_date DATE NOT NULL, 
	amount FLOAT NOT NULL, 
	description VARCHAR(300), 
	created_at DATETIME, 
	PRIMARY KEY (id)
);
CREATE TABLE invoice (
	id INTEGER NOT NULL, 
	invoice_number VARCHAR(50) NOT NULL, 
	client_name VARCHAR(200) NOT NULL, 
	amount_usd FLOAT NOT NULL, 
	amount_eur FLOAT NOT NULL, 
	exchange_rate FLOAT NOT NULL, 
	invoice_date DATE NOT NULL, 
	due_date DATE, 
	description TEXT, 
	quantity FLOAT, 
	unit_price_usd FLOAT, 
	notes TEXT, 
	status VARCHAR(20), 
	pdf_hash VARCHAR(64), 
	pdf_storage_key VARCHAR(500), 
	currency VARCHAR(10), 
	payment_method VARCHAR(100), 
	created_at DATETIME, 
	customer_id INTEGER, 
	bank_id INTEGER, 
	PRIMARY KEY (id), 
	UNIQUE (invoice_number), 
	FOREIGN KEY(customer_id) REFERENCES customer (id), 
	FOREIGN KEY(bank_id) REFERENCES bank (id)
);
CREATE TABLE expense (
	id INTEGER NOT NULL, 
	contractor_id INTEGER, 
	amount FLOAT NOT NULL, 
	currency VARCHAR(10), 
	category VARCHAR(100), 
	description TEXT, 
	expense_date DATE NOT NULL, 
	file_path VARCHAR(500), 
	invoice_number VARCHAR(100), 
	notes TEXT, 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(contractor_id) REFERENCES contractor (id)
);
CREATE TABLE invoice_item (
	id INTEGER NOT NULL, 
	invoice_id INTEGER NOT NULL, 
	description TEXT NOT NULL, 
	quantity FLOAT NOT NULL, 
	unit_price_usd FLOAT NOT NULL, 
	subtotal_usd FLOAT NOT NULL, 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(invoice_id) REFERENCES invoice (id)
);
CREATE TABLE module_enabled (
	id INTEGER NOT NULL, 
	module_id VARCHAR(100) NOT NULL, 
	enabled BOOLEAN, 
	PRIMARY KEY (id), 
	UNIQUE (module_id)
);
