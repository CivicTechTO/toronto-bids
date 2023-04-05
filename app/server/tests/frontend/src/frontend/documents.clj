(ns frontend.documents
	(:gen-class)
	(:require [clojure.string :as str])
	(:require [clojure.data.json :as json])
	(:require [hiccup.core :as hiccup])
	(:require [hiccup.page :as page])
	(:require [hiccup.form :as form])
	(:require [clj-http.client :as client])
	(:require [frontend.common :as common])
;	(:require [frontend.filter] :as filter)
)

(def TITLE "Call List")

(def DEFAULT-LIMIT 10)
(def DEFAULT-OFFSET 0)

(defn set-default [default value]
	(if (nil? value) default value)
)

(defn line1 [call]
	[:div (get call "short_description")]
)

(defn line2 [call]
	[:div
		[:div.item (get call "call_number")]
		[:div.item (get call "division")]
		[:div.item (get call "type")]
		[:div.item (get call "commodity")]
		[:div.item (get call "commodity_type")]
		[:div.item (get call "posting_date")]
		[:div.item (get call "closing_date")]
	]
)

(defn call-display [call]
	[:div.call (line1 call) (line2 call)]
)

(defn contents [api-base limit offset]
	(let
		[
			query {"limit" limit "offset" offset}
			response (client/get (str api-base "documents") {:query-params query :accept :json})
			body (get response :body)

			document-array (json/read-str body)
		]
		[:div (map call-display document-array)]
	)
)

(defn output [api-base local-base limit-arg offset-arg]
	(let
		[
			limit (set-default DEFAULT-LIMIT limit-arg)
			offset (set-default DEFAULT-OFFSET offset-arg)
		]
		(common/page api-base local-base TITLE (contents api-base limit offset))
	)
)


