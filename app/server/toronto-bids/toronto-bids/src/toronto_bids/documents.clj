(ns toronto-bids.documents
	(:gen-class)
	(:require [clojure.string :as str])
	(:require [clojure.java.jdbc :as jdbc])
	(:require [clojure.data.json :as json])
)

(def SEARCH-STRING 
	(str 
		"document.id IN (SELECT id FROM" 
		" (SELECT id, short_description, description, search_text FROM document" 
		" HAVING MATCH(short_description, description, search_text) AGAINST (? IN BOOLEAN MODE)) AS found)"
	)
)

; The argument-string
; (call_number type division commodity commodity_type posting_date_after posting_date_before closing_date_after closing_date_before buyer search-text) 

(def TEST-LIST 
	(list "call_number = ?" "type = ?" "division = ?" "commodity = ?" "commodity_type = ?" 
	"posting_date >= ?" "posting_date <= ?" "closing_date >= ?" "closing_date <= ?" "buyer = ?" SEARCH-STRING)
)

(def COLUMN-STRING " document.id AS document_id, division, type, call_number, commodity, commodity_type, short_description, posting_date, closing_date, site_meeting")

(def FROM-STRING
	(str " FROM document" 	
		" JOIN type ON document.type_id = type.id"
		" JOIN division ON document.division_id = division.id"
		" JOIN commodity_type ON document.commodity_type_id = commodity_type.id"
		" JOIN commodity ON commodity_type.commodity_id = commodity.id"
		" JOIN document_buyer ON document.id = document_id"
		" JOIN buyer ON buyer_id = buyer.id"
	)
)

(def HEAD (str "SELECT DISTINCT" COLUMN-STRING FROM-STRING " WHERE TRUE"))

(def ORDER-STRING " ORDER BY posting_date")

(def BUYER-SELECT 
	"SELECT buyer, phone, email, location FROM document_buyer JOIN buyer ON buyer_id = buyer.id JOIN location ON location_id = location.id WHERE document_id = ?;"
)

(defn limit-string [limit offset]
	(str 
		(if (nil? limit) "" (str " LIMIT " limit))
		(if (nil? offset) "" (str " OFFSET " offset))
	)
)

(defn make-query [test-list argument-list tail where-clause sql]
	(let 
		[
			test (first test-list)
			argument (first argument-list)
			test-rest (rest test-list)
			argument-rest (rest argument-list)
		]
		(cond 
			(nil? test) (assoc sql 0 (str HEAD where-clause tail))
			(not (nil? argument)) (make-query test-rest argument-rest tail (str where-clause " AND " test) (conj sql argument)) 
			:else (make-query test-rest argument-rest tail where-clause sql)
		)
	)
)

(defn make-insert-buyers [db]
	(fn [row]
		(let
			[document_id (get row :document_id)]
			(assoc row :buyers (jdbc/query db [BUYER-SELECT document_id]))
		)
	)
)

(defn fetch-documents [db argument-list limit offset]
	(let
		[
			tail (str ORDER-STRING (limit-string limit offset) ";")
			query (make-query TEST-LIST argument-list tail "" [""])
		]
		(map (make-insert-buyers db) (jdbc/query db query))
	)
)

(defn write-documents [db argument-list limit offset]
	(json/write-str (fetch-documents db argument-list limit offset))
)

(defn write-description [db document_id]
	(let [row (first (jdbc/query db ["SELECT description FROM document WHERE document.id = ?" document_id]))]
		(if row 
			(json/write-str (get row :description))
			(hash-map :status 404 :body "Document not found")
		)
	)
)
