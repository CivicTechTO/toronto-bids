(ns frontend.common
	(:gen-class)
	(:require [clojure.string :as str])
	(:require [clojure.data.json :as json])
	(:require [hiccup.core :as hiccup])
	(:require [hiccup.page :as page])
	(:require [hiccup.form :as form])
	(:require [clj-http.client :as client])
)

; (defn division-option [row]
; 	(let [value (get row :division)]
; 	)
; )

(defn option [value]
	[:option value]
)

(defn divisions [api-base]
	(let
		[
			response (client/get (str api-base "plain_divisions") {:accept :json})
			body (get response :body)

			division-list (json/read-str body)
		]
		[:div 
			(list 
				[:div.label "Divisions"]
				[:select {:name "division"}
					(map option division-list)
				]
			)
		]
	)
)

(defn select [api-base]
	[:div#select
		(divisions api-base)
	]
)

(defn display [api-base limit offset]
)


(defn body [title api-base contents]
	[:div#outer
		[:div#title title]
		[:div#wrapper
			(select api-base)
			[:div#contents
				contents
			]
		]
	]
)


(defn head [local-base title]
	[:head
		[:title title]
		(page/include-css (str local-base "styles.css"))
	]
)

(defn page [api-base local-base title contents]
	(page/html5 (list (head local-base title) [:body (body title api-base contents)]))
)
