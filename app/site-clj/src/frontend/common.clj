(ns frontend.common
	(:gen-class)
	(:require [clojure.data.json :as json])
	(:require [hiccup.page :as page])
	(:require [hiccup.util :as util])
	(:require [clj-http.client :as client])
)

(def title "Open Bids Toronto")

(def ALL "*All*")
(def DEFAULT-LIMIT "8")
(def DEFAULT-OFFSET "0")

(defn forward [limit offset]
	(+ offset limit)
)

(defn back [limit offset]
	(max 0 (- offset limit))
)

(defn parse-int
	([name value default] (parse-int name (or value default)))
	([name value]
		(try
			(Integer/parseInt value)
			(catch NumberFormatException exception
				(throw (Exception. (str "The " name " argument '" value "' is not a number")))
			)
		)
	)
)

(def head
	[:head
		[:title title]
		(page/include-css "styles.css")
	]
)

(defn make-query-params [division type commodity commodity_type buyer posting_date_before posting_date_after closing_date_before closing_date_after search_text limit offset]
	{
		"division" division
		"type" (or type ALL)
		"commodity" commodity
		"commodity_type" commodity_type
		"buyer" (or buyer ALL)
		"posting_date_before" posting_date_before
		"posting_date_after" posting_date_after
		"closing_date_before" closing_date_before
		"closing_date_after" closing_date_after
		"search_text" (if (nil? search_text) "" search_text)
		"limit" (str limit)
		"offset" (str offset)
	}
)

(def default-query-params
	(make-query-params
		ALL ALL ALL ALL ALL
		"" "" "" "" ""
		(parse-int "limit" DEFAULT-LIMIT)
		(parse-int "offset" DEFAULT-OFFSET)
	)
)

(defn api-call [api-base endpoint params]
	(->> params
		(util/url (str api-base endpoint))
		(util/to-str)
		(client/get)
		(:body)
		(json/read-str)
	)
)
