import numpy as np
import re, sys
import theano
import theano.tensor as T
from keras.utils.theano_utils import shared_zeros

import skipthoughts
import encoded_already
import nltk

#from wordvec_pruning import prune_statements
#from pos_pruning import prune_statements
from skip_thought_pruning import prune_thoughts


dtype=theano.config.floatX

#'''YOU NEED TO TURN THIS BACK ON
model = skipthoughts.load_model()
#'''


def init_shared_normal(num_rows, num_cols, scale=1):
    '''Initialize a matrix shared variable with normally distributed
    elements.'''
    return theano.shared(np.random.normal(
        scale=scale, size=(num_rows, num_cols)).astype(dtype))

def init_shared_normal_tensor(num_slices, num_rows, num_cols, scale=1):
    '''Initialize a matrix shared variable with normally distributed
    elements.'''
    return theano.shared(np.random.normal(
        scale=scale, size=(num_slices, num_rows, num_cols)).astype(dtype))

'''ORIGINAL
def init_shared_normal_tensor(num_slices, num_rows, num_cols, scale=1):
    #Initialize a matrix shared variable with normally distributed elements.
    return theano.shared(np.random.normal(
        scale=scale, size=(num_slices, num_rows, num_cols)).astype(dtype))
'''

def init_shared_zeros(*shape):
    '''Initialize a vector shared variable with zero elements.'''
    return theano.shared(np.zeros(shape, dtype=dtype))

def make_batches(size, batch_size):
    nb_batch = int(np.ceil(size/float(batch_size)))
    return [(i*batch_size, min(size, (i+1)*batch_size)) for i in range(0, nb_batch)]

def maxnorm_constraint(p, m=40):
    norms = T.sqrt(T.sum(T.sqr(p)))
    desired = T.clip(norms, 0, m)
    p = p * (desired / (1e-7 + norms))
    return p

def get_param_updates(params, grads, lr, method=None, **kwargs):
    rho = 0.95
    epsilon = 1e-6

    accumulators = [shared_zeros(p.get_value().shape) for p in params]
    updates=[]

    if 'constraint' in kwargs:
        constraint = kwargs['constraint']
    else:
        constraint = None

    if method == 'adadelta':
        print "Using ADADELTA"
        delta_accumulators = [shared_zeros(p.get_value().shape) for p in params]
        for p, g, a, d_a in zip(params, grads, accumulators, delta_accumulators):
            new_a = rho * a + (1 - rho) * g ** 2 # update accumulator

            # use the new accumulator and the *old* delta_accumulator
            update = g * T.sqrt(d_a + epsilon) / T.sqrt(new_a + epsilon)
            new_p = p - lr * update

            # update delta_accumulator
            new_d_a = rho * d_a + (1 - rho) * update ** 2

            updates.append((p, new_p))
            updates.append((a, new_a))
            updates.append((d_a, new_d_a))

    elif method == 'adagrad':
        print "Using ADAGRAD"
        for p, g, a in zip(params, grads, accumulators):
            new_a = a + g ** 2 # update accumulator

            new_p = p - lr * g / T.sqrt(new_a + epsilon)
            updates.append((p, new_p)) # apply constraints
            updates.append((a, new_a))

    elif method == 'momentum': # Default
        print "Using MOMENTUM"
        momentum = kwargs['momentum']
        for param, gparam in zip(params, grads):
            param_update = theano.shared(param.get_value()*0., broadcastable=param.broadcastable)
            gparam_constrained = maxnorm_constraint(gparam)
            param_update_update = momentum*param_update + (1. - momentum)*gparam_constrained
            updates.append((param, param - param_update * lr))
            updates.append((param_update, param_update_update))

    else: # Default
        print "Using DEFAULT"
        for param, gparam in zip(params, grads):
            param_update = maxnorm_constraint(gparam)
            updates.append((param, param - param_update * lr))

    # apply constraints on self.weights update
    # assumes that updates[0] corresponds to self.weights param
    if constraint != None:
        updates[0] = (updates[0][0], constraint(updates[0][1]))
        print "no constraint"
    else:
        print "yes constraint"

    return updates


def compute_bow(input_str, word_to_id, num_words):
    bow = np.zeros((num_words,))
    for token in input_str.split():
        bow[word_to_id[token]] += 1
    return bow

def compute_seq(input_str, word_to_id, num_words):
    seq = []
    for token in input_str.split():
        seq.append(word_to_id[token])
    return seq

def transform_ques(question, word_to_id, num_words):
    question.append(compute_seq(question[2], word_to_id, num_words))
    question[2] = compute_bow(question[2], word_to_id, num_words)
    return question

def parse_dataset(input_file, word_id=0, word_to_id={}, update_word_ids=True):
    dataset = []
    questions = []
    with open(input_file) as f:
        statements = []
        article_no = 0
        line_no = 0
        stmt_to_line = {}
        for line in f:
            line = line.strip()
            if len(line) > 0 and line[:2] == '1 ' and len(statements) > 0: # new article
                dataset.append(statements)
                statements = []
                line_no = 0
                stmt_to_line = {}
                article_no += 1
            if '\t' in line:
                question_parts = line.split('\t')
                tokens = re.sub(r'([\.\?])$', r' \1', question_parts[0].strip()).split()
                if update_word_ids:
                    for token in tokens[1:]:
                        if token not in word_to_id:
                            word_to_id[token] = word_id
                            word_id += 1

                # To handle the case of "3 6"
                lines = None
                if ' ' in question_parts[2]:
                    stmts = question_parts[2].split(' ')
                    lines = ''
                    for stmt in stmts:
                        lines += str(stmt_to_line[stmt]) + ' '
                    lines = lines.strip()
                else:
                    lines = str(stmt_to_line[question_parts[2]])

                questions.append([article_no, line_no, ' '.join(tokens[1:]), word_to_id[question_parts[1]], lines])
            else:
                tokens = re.sub(r'([\.\?])$', r' \1', line).split()
                stmt_to_line[tokens[0]] = line_no
                if update_word_ids:
                    for token in tokens[1:]:
                        if token not in word_to_id:
                            word_to_id[token] = word_id
                            word_id += 1
                statements.append(' '.join(tokens[1:]))
                line_no += 1
        if len(statements) > 0:
            dataset.append(statements)
    dataset_bow = map(lambda y: map(lambda x: compute_bow(x, word_to_id, word_id), y), dataset)
    dataset_seq = map(lambda y: map(lambda x: compute_seq(x, word_to_id, word_id), y), dataset)
    questions_bow = map(lambda x: transform_ques(x, word_to_id, word_id), questions)
    return dataset_seq, dataset_bow, questions_bow, word_to_id, word_id

def pad_statement(stmt, null_word, max_words=4800):
    if len(stmt) >= max_words:
        return stmt[-max_words:]
    else:
        return stmt + [null_word for i in range(max_words - len(stmt))]

def pad_memories(stmts, null_word, max_stmts=20, max_words=4800):
    if len(stmts) >= max_words:
        return stmts[-max_stmts:]
    else:

        return stmts + [[null_word for j in range(max_words)] for i in range(max_stmts - len(stmts))]

'''ORIGINAL
def parse_dataset_weak(input_file, word_id=0, word_to_id={}, update_word_ids=True, max_stmts=20, max_words=20):
    dataset = []
    questions = []
    null_word = '<NULL>'
    if null_word not in word_to_id:
        if update_word_ids == True:
            word_to_id[null_word] = word_id
            word_id += 1
        else:
            print "Null word not found!! AAAAA"
            sys.exit(1)
    null_word_id = word_to_id[null_word]

    with open(input_file) as f:
        statements = []
        article_no = 0
        line_no = 0
        stmt_to_line = {}
        #g = f.read()
        for line in f:
            line = line.strip()
            if len(line) > 0 and line[:2] == '1 ' and len(statements) > 0: # new article
                dataset.append(statements)
                statements = []
                line_no = 0
                stmt_to_line = {}
                article_no += 1'''
            #if '\t' in line:
            #   question_parts = line.split('\t')
'''             tokens = re.sub(r'([\.\?])$', r' \1', question_parts[0].strip()).split()
                if update_word_ids:
                    for token in tokens[1:]:
                        if token not in word_to_id:
                            word_to_id[token] = word_id
                            word_id += 1

                padded_stmts = pad_memories(statements[:line_no], null_word, max_stmts, max_words)
                padded_ques = pad_statement(tokens[1:], null_word, max_words)
                questions.append([article_no, line_no, padded_stmts, padded_ques, question_parts[1]])
            else:
                tokens = re.sub(r'([\.\?])$', r' \1', line).split()
                stmt_to_line[tokens[0]] = line_no
                if update_word_ids:
                    for token in tokens[1:]:
                        if token not in word_to_id:
                            word_to_id[token] = word_id
                            word_id += 1
                statements.append(pad_statement(tokens[1:], null_word, max_words))
                line_no += 1
        if len(statements) > 0:
            dataset.append(statements)
    #questions = prune_statements(dataset, questions)
    #print dataset
    q = questions
    #print q
    #q = prune_thoughts(dataset, questions, input_file)
    questions_seq = map(lambda x: transform_ques_weak(x, word_to_id, word_id), questions)
    return dataset, questions_seq, word_to_id, word_id, null_word_id
'''

def parse_dataset_weak(input_file, word_id=0, word_to_id={}, update_word_ids=True, max_stmts=20, max_words=4800):
    dataset = []
    questions = []
    null_word = '<NULL>'
    if null_word not in word_to_id:
        if update_word_ids == True:
            word_to_id[null_word] = word_id
            word_id += 1
        else:
            print "Null word not found!! AAAAA"
            sys.exit(1)
    null_word_id = word_to_id[null_word]

    with open(input_file) as f:
        statements = []
        article_no = 0
        line_no = 0
        stmt_to_line = {}
        sqa = 0
        sqa_all = []
        #g = f.read()
               
        for line in f:    
            line = line.strip()
            if len(line) > 0 and line[:2] == '1 ' and len(statements) > 0: # new article
                #clean1 = re.sub("[0-9]", "", line)
                #sent_detector = nltk.data.load('tokenizers/punkt/english.pickle')
                #dataset = sent_detector.tokenize(clean1)

                dataset.append(statements)
                statements = []
                line_no = 0
                stmt_to_line = {}
                article_no += 1
            if '\t' in line:
                
                sent_detector = nltk.data.load('tokenizers/punkt/english.pickle')
                qa = line.split('\t')
                #qa = line
                '''print "qa"
                print qa
                '''
                clean2 = re.sub("[0-9]", "", qa[0])
                q_parts = sent_detector.tokenize(clean2)
                #questions[sqa].append(qa_parts[0])
                clean2 = re.sub("[0-9]", "", qa[1])
                a_parts = sent_detector.tokenize(clean2)
                #questions_answers_only.append([q_parts, a_parts])
                sqa += 1

                #sqa_all[2].append(statements)
                #sqa_all[3].append(questions_answers_only[0][0])
                #sqa_all[4].append(questions_answers_only[0][1])
                #sqa_all[3].append(q_parts)
                #sqa_all[4].append(a_parts)

                #questions_answers_only = []
                
                question_parts = line.split('\t')
                tokens = re.sub(r'([\.\?])$', r' \1', question_parts[0].strip()).split()
                if update_word_ids:
                    for token in tokens[1:]:
                        if token not in word_to_id:
                            word_to_id[token] = word_id
                            word_id += 1

                padded_stmts = pad_memories(statements[:line_no], null_word, max_stmts, max_words)
                padded_ques = pad_statement(tokens[1:], null_word, max_words)
                #questions.append([article_no, line_no, padded_stmts, padded_ques, question_parts[1]])
                sqa_all.append([article_no, line_no, statements[:line_no], q_parts, a_parts])
                '''print "article_no"
                print article_no
                print "line_no"
                print line_no
                print "questions"
                print q_parts
                print a_parts
                print "statements"
                print statements
                '''
            else:
                clean3 = re.sub("[0-9]", "", line)
                sent_detector = nltk.data.load('tokenizers/punkt/english.pickle')
                statement = sent_detector.tokenize(clean3)
                #statements.append(statement)
                statements += statement

                tokens = re.sub(r'([\.\?])$', r' \1', line).split()
                stmt_to_line[tokens[0]] = line_no
                if update_word_ids:
                    for token in tokens[1:]:
                        if token not in word_to_id:
                            word_to_id[token] = word_id
                            word_id += 1

                

                #statements.append(pad_statement(tokens[1:], null_word, max_words))
                line_no += 1
        if len(statements) > 0:
            dataset.append(statements)
    #questions = prune_statements(dataset, questions)
    
    '''
    print "dataset"
    print dataset
    print "questions"
    print q_parts
    print a_parts
    print "statements"
    print statements
    '''

    #q = prune_thoughts(dataset, questions, input_file)

    '''
    print "word_to_id"
    print word_to_id
    print "word_id"
    print word_id
    print "tokens"
    print tokens
    print "stmt_to_line"
    print stmt_to_line
    '''

    print "sqa_all"
    #print sqa_all
    '''print "sqa_all[2][3]"
    print sqa_all[2][3]
    print "sqa_all[0][3]"
    print sqa_all[0][3]
'''

    question = sqa_all
    #question = map(lambda x: transform_ques_weak(x, word_to_id, word_id, sqa), sqa_all)
    questions_seq = transform_ques_weak(question, sqa, word_to_id)
    print "questions_seq"
    #print questions_seq

    return dataset, questions_seq, word_to_id, word_id, null_word_id   

'''
def transform_ques_weak(question, word_to_id, num_words):
    indices = []
    for stmt in question[2]:
        index_stmt = map(lambda x: word_to_id[x], stmt)
        indices.append(index_stmt)
    question[2] = indices
    question[3] = map(lambda x: word_to_id[x], question[3])
    question[4] = word_to_id[question[4]]
    return question
'''


def transform_ques_weak(x, sqa, word_to_id):
    '''print "x[0]"
    print x[0]
    print "x[1]"
    print x[1]
    print "x[2]"
    print x[2]
    print "x[3]"
    print x[3]
    print "x[4]"
    print x[4]
    #print "x"
    #print x
    print "x[13][2]"
    print x[13][2]
    print "x[13][3]"
    print x[13][3]
    print "x[13][4]"
    print x[13][4]
    
    print "word_to_id"
    print word_to_id
    print "x[4]"
    print x[4]
    print "x[0][4]"
    print x[0][4]
    '''
    d1 = [[14, 11, 21, 3, 4, 27, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 
        [1, 11, 21, 3, 4, 5, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 
        [20, 2, 3, 4, 12, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 
        [20, 28, 3, 4, 26, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 
        [31, 2, 3, 4, 26, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 
        [14, 39, 4, 9, 10, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 
        [20, 2, 3, 4, 27, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 
        [14, 13, 4, 9, 3, 20, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 
        [20, 13, 4, 9, 3, 14, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 
        [14, 35, 4, 9, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]]

    d2 = [[14, 11, 21, 3, 4, 27, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 
        [1, 11, 21, 3, 4, 5, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 
        [20, 2, 3, 4, 12, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 
        [20, 28, 3, 4, 26, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 
        [31, 2, 3, 4, 26, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 
        [14, 39, 4, 9, 10, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 
        [20, 2, 3, 4, 27, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]]



    e = [14, 36, 4, 9, 3, 1, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

    z = np.zeros((110,4800))

    #model = skipthoughts.load_model()
    quest = x
    '''
    indices = []
    for stmt in quest[2]:
        index_stmt = map(lambda x: word_to_id[x], stmt)
        indices.append(index_stmt)
    quest[2] = indices
    '''
    indices = []
    for i in range(0, sqa):
        
        qi2 = skipthoughts.encode(model, x[i][2])
        
        s = qi2.shape[0]
        z[:s] = qi2
        quest[i][2] = z.tolist()
        

        '''
        four = encoded_already.s1()
        quest[i][2] = four[0]
        '''

        #print "quest[i][2]"
        #print quest[i][2]
        

        
        #quest[i][2] = d1

        
        #quest[3][2] = d2
        #quest[13][2] = d2

        
        
        
        quest[i][3] = skipthoughts.encode(model, x[i][3])

        q3l = quest[i][3].tolist()
        quest[i][3] = q3l[0]    #because skipthoughts automatically puts two brackets around a single sentene encoding
        

        '''
        four2 = encoded_already.s2()
        quest[i][3] = four2[0]
        '''

        #quest[i][3] = e

        #quest[i][4] = skipthoughts.encode(model, x[i][4])
        #quest[i][4] = quest[i][4].tolist()
        quest[i][4] = word_to_id[x[i][4][0]]

        i += 1

    return quest

'''
if __name__ == "__main__":
    train_file = sys.argv[1]
    test_file = train_file.replace('train', 'test')

    train_dataset, train_questions, word_to_id, num_words = parse_dataset_weak(train_file)
    test_dataset, test_questions, _, _ = parse_dataset_weak(test_file, word_id=num_words, word_to_id=word_to_id, update_word_ids=False)

    # each element of train_questions contains: [article_no, line_no, [lists of indices of statements and question], index of answer word]
    print train_questions[0]
'''
