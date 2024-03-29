(ns toronto-bids.documents
	(:gen-class)
	(:require [clojure.string :as string])
	(:require [clojure.java.jdbc :as jdbc])
	(:require [clojure.data.json :as json])
	(:require [ring.util.response :as response])
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

(def COLUMN-STRING " document.id AS document_id, division, type, call_number, commodity, commodity_type, short_description, posting_date, closing_date")

(def DETAIL-COLUMNS (str COLUMN-STRING ",site_meeting,description,buyer"))

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

(def ORDER-STRING " ORDER BY posting_date DESC")

(def BUYER-SELECT 
	"SELECT buyer, phone, email, location FROM document_buyer JOIN buyer ON buyer_id = buyer.id JOIN location ON location_id = location.id WHERE document_id = ?;"
)

(def DETAILS_SQL
	(str "	SELECT " DETAIL-COLUMNS FROM-STRING " WHERE call_number = ?")
)

(def ATTACH_SQL
	(str "SELECT filename FROM attachments WHERE call_number = ?")
)

(defn limit-string [limit offset]
	(str 
		(if (nil? limit) "" (str " LIMIT " limit))
		(if (nil? offset) "" (str " OFFSET " offset))
	)
)

(defn collapse [fn initial coll]
 	(cond 
 		(empty? coll) initial
		:else (collapse	fn (fn initial (first coll)) (next coll))
	)
)

(defn join [test argument]
	{
		:test test, 
		:argument argument
	}
)

(defn pick [input]
	(not (nil? (get input :argument)))
)

(defn construct [previous entry]
	(let
		[
			test (get entry :test)
			argument (get entry :argument)
			sql (str (get previous 0) " AND " test)
			result (assoc previous 0 sql)
		]
		(conj result argument)
	)
)

(defn make-query [test-list argument-list tail]
	(let
		[
			input (map join test-list argument-list)
			active (filter pick input)
			result (collapse construct [HEAD] active)
			sql (get result 0)
		]
		(assoc result 0 (str sql tail))
	)
)

(defn parse [name string]
	(try 
		(Integer/parseInt string)
		(catch NumberFormatException exception (throw (Exception. (str name "=" string " is not a number"))))
	)
)

(defn block-field [query name test value-string]
	(cond 
		(nil? value-string) query
		:else (conj (assoc query 0 (str (get query 0) test)) (parse name value-string))
	)
)

(defn validate [limit-string offset-string]
	(if (and (nil? limit-string) (not (nil? offset-string))) (throw (Exception. "Cannot set offset without limit")))
)

(defn set-limit [query limit-string offset-string]
	(validate limit-string offset-string)
	(block-field (block-field query "limit" " LIMIT ?" limit-string) "offset" " OFFSET ?" offset-string)
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
			query (make-query TEST-LIST argument-list tail)
			result (jdbc/query db query)
		]
		(map (make-insert-buyers db) result)
	)
)

(defn output-attachments [db call_number]
	(json/write-str (jdbc/query db [ATTACH_SQL call_number]))
)

(defn output-documents [db argument-list limit offset]
	(json/write-str (fetch-documents db argument-list limit offset))
)

(defn output-details [db call_number]
	(let 
		[
			row (first (jdbc/query db [DETAILS_SQL call_number]))
		]
		(if row 
			(response/response row)
			(response/status response/response("Document not found") 404)
		)
	)
)

